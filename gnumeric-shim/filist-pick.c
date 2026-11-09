/* filist-pick: LD_PRELOAD-Shim, der Gnumerics (GTK3-)Dateidialog an filists Portal umleitet.
 *
 * Gnumeric/goffice bauen einen GtkFileChooserDialog (g_object_new) und zeigen ihn über
 * go_gtk_file_sel_dialog() – GTK leitet so etwas nie ans Portal. Wir ersetzen genau diese eine Funktion:
 * Zustand des Dialogs (Aktion, Filter, Name, Ordner, Dateityp-Combo) → org.freedesktop.portal.FileChooser,
 * Ergebnis zurück in den (nie gezeigten) Dialog; get_uri/get_uris/get_choice liefern dann das Portal-Ergebnis.
 * Bei jedem Problem (kein Portal, unbekannte Aktion) läuft die Originalfunktion → Gnumeric bleibt benutzbar.
 * Debug: FILIST_SHIM_DEBUG=1
 */
#define _GNU_SOURCE
#include <gtk/gtk.h>
#include <gio/gio.h>
#include <dlfcn.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>

#define DATA_URIS "fpick-uris"
#define DATA_CHOICES "fpick-choices"
#define DBG(...) do { if (g_getenv("FILIST_SHIM_DEBUG")) g_printerr("[filist-pick] " __VA_ARGS__); } while (0)

/* ---------- Helfer ---------- */
static void find_combo_cb(GtkWidget *w, gpointer data)
{
	GtkWidget **found = data;
	if (*found)
		return;
	if (GTK_IS_COMBO_BOX(w) && !GTK_IS_FILE_CHOOSER(w))
		*found = w;
	else if (GTK_IS_CONTAINER(w))
		gtk_container_forall(GTK_CONTAINER(w), find_combo_cb, data);
}

static GtkWidget *find_combo(GtkWidget *root)
{
	GtkWidget *found = NULL;
	if (root)
		find_combo_cb(root, &found);
	return found;
}

static void find_advanced_cb(GtkWidget *w, gpointer data)
{
	GtkWidget **found = data;
	if (!*found && g_object_get_data(G_OBJECT(w), "extra"))
		*found = g_object_get_data(G_OBJECT(w), "extra");
}

static GVariant *combo_options(GtkComboBox *combo)
{
	GtkTreeModel *model = gtk_combo_box_get_model(combo);
	GtkTreeIter it;
	GVariantBuilder b;
	int i = 0;
	g_variant_builder_init(&b, G_VARIANT_TYPE("a(ss)"));
	if (model && gtk_tree_model_get_iter_first(model, &it)) {
		do {
			char *text = NULL;
			char id[16];
			gtk_tree_model_get(model, &it, 0, &text, -1);
			g_snprintf(id, sizeof id, "%d", i++);
			g_variant_builder_add(&b, "(ss)", id, text ? text : "");
			g_free(text);
		} while (gtk_tree_model_iter_next(model, &it));
	}
	return g_variant_builder_end(&b);
}

typedef struct {
	GMainLoop *loop;
	GVariant *result;     /* a{sv} oder NULL */
	guint response;
	guint sub;
	GDBusConnection *conn;
} Wait;

static void on_response(GDBusConnection *c, const char *s, const char *p, const char *i, const char *sig,
			GVariant *params, gpointer data)
{
	Wait *w = data;
	GVariant *res = NULL;
	g_variant_get(params, "(u@a{sv})", &w->response, &res);
	w->result = res;
	g_main_loop_quit(w->loop);
}

/* Portal fragen. Rückgabe: TRUE bei Antwort (response in *resp, results in *res), FALSE bei Portal-Fehler */
static gboolean ask_portal(const char *method, const char *title, GVariant *options_floating,
			   const char *token, guint *resp, GVariant **res)
{
	GError *err = NULL;
	GDBusConnection *conn = g_bus_get_sync(G_BUS_TYPE_SESSION, NULL, &err);
	if (!conn) {
		DBG("kein Bus: %s\n", err->message);
		g_clear_error(&err);
		return FALSE;
	}
	char *sender = g_strdup(g_dbus_connection_get_unique_name(conn) + 1);
	for (char *c = sender; *c; c++)
		if (*c == '.')
			*c = '_';
	char *path = g_strdup_printf("/org/freedesktop/portal/desktop/request/%s/%s", sender, token);
	Wait w = {g_main_loop_new(NULL, FALSE), NULL, 2, 0, conn};
	w.sub = g_dbus_connection_signal_subscribe(conn, "org.freedesktop.portal.Desktop",
		"org.freedesktop.portal.Request", "Response", path, NULL, G_DBUS_SIGNAL_FLAGS_NONE,
		on_response, &w, NULL);
	GVariant *ret = g_dbus_connection_call_sync(conn, "org.freedesktop.portal.Desktop",
		"/org/freedesktop/portal/desktop", "org.freedesktop.portal.FileChooser", method,
		g_variant_new("(ss@a{sv})", "", title, options_floating), NULL, G_DBUS_CALL_FLAGS_NONE,
		-1, NULL, &err);
	gboolean ok = FALSE;
	if (!ret) {
		DBG("Portalaufruf fehlgeschlagen: %s\n", err->message);
		g_clear_error(&err);
	} else {
		g_variant_unref(ret);
		g_main_loop_run(w.loop);      /* GTK-Hauptschleife läuft darin weiter (Fenster bleibt lebendig) */
		*resp = w.response;
		*res = w.result;
		ok = TRUE;
	}
	g_dbus_connection_signal_unsubscribe(conn, w.sub);
	g_main_loop_unref(w.loop);
	g_free(sender);
	g_free(path);
	g_object_unref(conn);
	return ok;
}

static void clear_result(GtkWidget *d)
{
	g_object_set_data(G_OBJECT(d), DATA_URIS, NULL);
	g_object_set_data(G_OBJECT(d), DATA_CHOICES, NULL);
}


/* ---------- Setter mitschreiben (ein nie gezeigter GTK-Dialog liefert get_uri/get_current_name nicht zurück) ---------- */
static void remember(gpointer o, const char *key, const char *val)
{
	if (G_IS_OBJECT(o))
		g_object_set_data_full(G_OBJECT(o), key, g_strdup(val), g_free);
}

static void cur_from_uri(gpointer fc, const char *uri);
static void cur_with_name(gpointer fc, const char *name);

gboolean gtk_file_chooser_set_uri(GtkFileChooser *fc, const char *uri)
{
	static gboolean (*real)(GtkFileChooser *, const char *);
	if (!real) real = dlsym(RTLD_NEXT, "gtk_file_chooser_set_uri");
	remember(fc, "fpick-seturi", uri);
	cur_from_uri(fc, uri);
	return real(fc, uri);
}

gboolean gtk_file_chooser_select_uri(GtkFileChooser *fc, const char *uri)
{
	static gboolean (*real)(GtkFileChooser *, const char *);
	if (!real) real = dlsym(RTLD_NEXT, "gtk_file_chooser_select_uri");
	remember(fc, "fpick-seturi", uri);
	return real(fc, uri);
}

/* "fpick-cur": die URI, die ein GTK-Dialog liefern würde (ein nie gezeigter Dialog gibt get_uri nicht her,
 * Gnumerics changed-Handler des Dateityps verlässt sich aber darauf, um die Endung zu tauschen). */
static void cur_from_uri(gpointer fc, const char *uri)
{
	remember(fc, "fpick-cur", uri);
}

static void cur_with_name(gpointer fc, const char *name)
{
	const char *cur = g_object_get_data(G_OBJECT(fc), "fpick-cur");
	if (!cur)
		cur = g_object_get_data(G_OBJECT(fc), "fpick-setfolder");
	if (!cur || !name)
		return;
	GFile *f = g_file_new_for_uri(cur);
	GFile *dir = g_file_query_exists(f, NULL) && g_file_query_file_type(f, 0, NULL) == G_FILE_TYPE_DIRECTORY
		     ? g_object_ref(f) : g_file_get_parent(f);
	if (dir) {
		GFile *c = g_file_get_child(dir, name);
		char *u = g_file_get_uri(c);
		remember(fc, "fpick-cur", u);
		g_free(u);
		g_object_unref(c);
		g_object_unref(dir);
	}
	g_object_unref(f);
}

void gtk_file_chooser_set_current_name(GtkFileChooser *fc, const char *name)
{
	static void (*real)(GtkFileChooser *, const char *);
	if (!real) real = dlsym(RTLD_NEXT, "gtk_file_chooser_set_current_name");
	remember(fc, "fpick-setname", name);
	cur_with_name(fc, name);
	real(fc, name);
}

gboolean gtk_file_chooser_set_current_folder_uri(GtkFileChooser *fc, const char *uri)
{
	static gboolean (*real)(GtkFileChooser *, const char *);
	if (!real) real = dlsym(RTLD_NEXT, "gtk_file_chooser_set_current_folder_uri");
	remember(fc, "fpick-setfolder", uri);
	if (!g_object_get_data(G_OBJECT(fc), "fpick-cur"))
		cur_from_uri(fc, uri);
	return real(fc, uri);
}

/* ---------- die ersetzte Funktion ---------- */
typedef gboolean (*sel_fn)(GtkWindow *, GtkWidget *);

static char *last_save_uri;      /* vom Portal bestätigte Speicher-URI (filist hat das Überschreiben schon erfragt) */
static gboolean pick_impl(GtkWindow *parent, GtkWidget *dialog, sel_fn real);

gboolean go_gtk_file_sel_dialog(GtkWindow *parent, GtkWidget *dialog)
{
	static sel_fn real;
	if (!real)
		real = (sel_fn)dlsym(RTLD_NEXT, "go_gtk_file_sel_dialog");
	if (g_getenv("FILIST_SHIM_OFF") || !GTK_IS_FILE_CHOOSER_DIALOG(dialog))
		return real(parent, dialog);
	g_object_set_data(G_OBJECT(dialog), "fpick-live", GINT_TO_POINTER(1));
	gboolean r = pick_impl(parent, dialog, real);
	g_object_set_data(G_OBJECT(dialog), "fpick-live", NULL);
	return r;
}

/* Gnumeric fragt nach dem Dialog selbst „Datei ersetzen?“ – hat filist das gerade getan, nicht doppelt fragen */
gboolean go_gtk_url_is_writeable(GtkWindow *parent, const char *uri, gboolean overwrite)
{
	static gboolean (*real)(GtkWindow *, const char *, gboolean);
	if (!real)
		real = dlsym(RTLD_NEXT, "go_gtk_url_is_writeable");
	if (last_save_uri && uri && !strcmp(uri, last_save_uri)) {
		g_clear_pointer(&last_save_uri, g_free);
		return TRUE;
	}
	return real(parent, uri, overwrite);
}

static gboolean pick_impl(GtkWindow *parent, GtkWidget *dialog, sel_fn real)
{

	GtkFileChooser *fc = GTK_FILE_CHOOSER(dialog);
	GtkFileChooserAction action = gtk_file_chooser_get_action(fc);
	const char *method;
	if (action == GTK_FILE_CHOOSER_ACTION_OPEN || action == GTK_FILE_CHOOSER_ACTION_SELECT_FOLDER)
		method = "OpenFile";
	else if (action == GTK_FILE_CHOOSER_ACTION_SAVE)
		method = "SaveFile";
	else {
		g_object_set_data(G_OBJECT(dialog), "fpick-live", NULL);
		return real(parent, dialog);
	}
	clear_result(dialog);

	GVariantBuilder o;
	g_variant_builder_init(&o, G_VARIANT_TYPE_VARDICT);
	static guint counter;
	char *token = g_strdup_printf("filistpick%d_%u", (int)getpid(), counter++);
	g_variant_builder_add(&o, "{sv}", "handle_token", g_variant_new_string(token));
	g_variant_builder_add(&o, "{sv}", "modal", g_variant_new_boolean(TRUE));

	/* Beschriftung des Bestätigen-Knopfs */
	GtkWidget *okb = gtk_dialog_get_widget_for_response(GTK_DIALOG(dialog), GTK_RESPONSE_OK);
	if (!okb)
		okb = gtk_dialog_get_widget_for_response(GTK_DIALOG(dialog), GTK_RESPONSE_ACCEPT);
	if (okb && GTK_IS_BUTTON(okb) && gtk_button_get_label(GTK_BUTTON(okb)))
		g_variant_builder_add(&o, "{sv}", "accept_label",
				      g_variant_new_string(gtk_button_get_label(GTK_BUTTON(okb))));

	if (action == GTK_FILE_CHOOSER_ACTION_SELECT_FOLDER)
		g_variant_builder_add(&o, "{sv}", "directory", g_variant_new_boolean(TRUE));
	if (action != GTK_FILE_CHOOSER_ACTION_SAVE && gtk_file_chooser_get_select_multiple(fc))
		g_variant_builder_add(&o, "{sv}", "multiple", g_variant_new_boolean(TRUE));

	/* Filter */
	GSList *fl = gtk_file_chooser_list_filters(fc);
	GtkFileFilter *curf = gtk_file_chooser_get_filter(fc);
	if (fl) {
		GVariantBuilder fb;
		g_variant_builder_init(&fb, G_VARIANT_TYPE("a(sa(us))"));
		for (GSList *l = fl; l; l = l->next) {
			GVariant *v = gtk_file_filter_to_gvariant(l->data);
			g_variant_builder_add_value(&fb, v);
			if (l->data == curf)
				g_variant_builder_add(&o, "{sv}", "current_filter", v);
		}
		g_variant_builder_add(&o, "{sv}", "filters", g_variant_builder_end(&fb));
	}

	/* Name + Ordner (aus den mitgeschriebenen Setter-Werten, sonst aus dem Dialog) */
	char *uri = gtk_file_chooser_get_uri(fc);
	if (!uri) {
		const char *su = g_object_get_data(G_OBJECT(dialog), "fpick-seturi");
		uri = su ? g_strdup(su) : NULL;
	}
	char *folder_uri = gtk_file_chooser_get_current_folder_uri(fc);
	if (!folder_uri) {
		const char *sf = g_object_get_data(G_OBJECT(dialog), "fpick-setfolder");
		folder_uri = sf ? g_strdup(sf) : NULL;
	}
	char *fpath = NULL;
	if (folder_uri)
		fpath = g_filename_from_uri(folder_uri, NULL, NULL);
	if (!fpath && uri) {
		char *p = g_filename_from_uri(uri, NULL, NULL);
		if (p) {
			fpath = g_path_get_dirname(p);
			g_free(p);
		}
	}
	if (action == GTK_FILE_CHOOSER_ACTION_SAVE) {
		char *name = gtk_file_chooser_get_current_name(fc);
		if (!name) {
			const char *sn = g_object_get_data(G_OBJECT(dialog), "fpick-setname");
			name = sn ? g_strdup(sn) : NULL;
		}
		if (!name && uri) {
			char *p = g_filename_from_uri(uri, NULL, NULL);
			name = p ? g_path_get_basename(p) : NULL;
			g_free(p);
		}
		if (name)
			g_variant_builder_add(&o, "{sv}", "current_name", g_variant_new_string(name));
		g_free(name);
	}
	if (fpath)
		g_variant_builder_add(&o, "{sv}", "current_folder", g_variant_new_bytestring(fpath));
	DBG("action=%d uri=%s folder=%s\n", action, uri ? uri : "-", fpath ? fpath : "-");

	/* Auswahlfelder: Dateityp-Combo (Extra-Widget bzw. „Erweitert“) + „schreibgeschützt“ */
	GtkWidget *combo = find_combo(gtk_file_chooser_get_extra_widget(fc));
	if (!combo && action == GTK_FILE_CHOOSER_ACTION_OPEN) {
		GtkWidget *adv = NULL;
		GtkWidget *area = gtk_dialog_get_action_area(GTK_DIALOG(dialog));
		if (area)
			gtk_container_forall(GTK_CONTAINER(area), find_advanced_cb, &adv);
		combo = find_combo(adv);
	}
	int combo_start = -1;
	GVariantBuilder cb;
	g_variant_builder_init(&cb, G_VARIANT_TYPE("a(ssa(ss)s)"));
	gboolean have_choices = FALSE;
	if (combo && gtk_widget_get_sensitive(combo)) {
		combo_start = gtk_combo_box_get_active(GTK_COMBO_BOX(combo));
		char init[16];
		g_snprintf(init, sizeof init, "%d", combo_start < 0 ? 0 : combo_start);
		g_variant_builder_add(&cb, "(ss@a(ss)s)", "ftype", "_Dateityp",
				      combo_options(GTK_COMBO_BOX(combo)), init);
		have_choices = TRUE;
	}
	if (action == GTK_FILE_CHOOSER_ACTION_OPEN && gtk_file_chooser_get_select_multiple(fc)) {
		g_variant_builder_add(&cb, "(ss@a(ss)s)", "read-only", "Nur _lesen",
				      g_variant_new_array(G_VARIANT_TYPE("(ss)"), NULL, 0), "false");
		have_choices = TRUE;
	}
	if (have_choices)
		g_variant_builder_add(&o, "{sv}", "choices", g_variant_builder_end(&cb));
	else
		g_variant_builder_clear(&cb);

	const char *title = gtk_window_get_title(GTK_WINDOW(dialog));
	guint resp = 2;
	GVariant *res = NULL;
	if (!ask_portal(method, title ? title : "", g_variant_builder_end(&o), token, &resp, &res)) {
		g_free(token); g_free(uri); g_free(folder_uri); g_free(fpath);
		g_slist_free(fl);
		g_object_set_data(G_OBJECT(dialog), "fpick-live", NULL);
		return real(parent, dialog);            /* Portal tot → normaler GTK-Dialog */
	}
	g_free(token); g_free(folder_uri); g_free(fpath);
	g_slist_free(fl);

	if (resp != 0 || !res) {
		g_free(uri);
		if (res) g_variant_unref(res);
		return FALSE;
	}

	/* Ergebnis auswerten */
	GVariant *uris_v = g_variant_lookup_value(res, "uris", G_VARIANT_TYPE("as"));
	char **uris = uris_v ? g_variant_dup_strv(uris_v, NULL) : NULL;
	if (uris_v) g_variant_unref(uris_v);
	if (!uris || !uris[0]) {
		g_strfreev(uris);
		g_free(uri);
		g_variant_unref(res);
		return FALSE;
	}

	GHashTable *ch = g_hash_table_new_full(g_str_hash, g_str_equal, g_free, g_free);
	GVariant *chv = g_variant_lookup_value(res, "choices", G_VARIANT_TYPE("a(ss)"));
	int combo_new = combo_start;
	if (chv) {
		GVariantIter it;
		const char *k, *v;
		g_variant_iter_init(&it, chv);
		while (g_variant_iter_next(&it, "(&s&s)", &k, &v)) {
			g_hash_table_insert(ch, g_strdup(k), g_strdup(v));
			if (!strcmp(k, "ftype"))
				combo_new = atoi(v);
		}
		g_variant_unref(chv);
	}
	g_free(uri);

	if (action == GTK_FILE_CHOOSER_ACTION_SAVE) {
		/* Name im echten Dialog setzen, dann Dateityp umschalten: Gnumerics changed-Handler passt die
		 * Endung an (wie im GTK-Dialog) – danach lesen wir die fertige URI zurück. */
		char *p = g_filename_from_uri(uris[0], NULL, NULL);
		if (p) {
			char *base = g_path_get_basename(p);
			gtk_file_chooser_set_uri(fc, uris[0]);
			gtk_file_chooser_set_current_name(fc, base);
			g_free(base);
			if (combo && combo_new >= 0 && combo_new != combo_start)
				gtk_combo_box_set_active(GTK_COMBO_BOX(combo), combo_new);
			char *final = gtk_file_chooser_get_uri(fc);
			if (final && g_str_has_prefix(final, "file:")) {
				g_free(uris[0]);
				uris[0] = final;
			} else
				g_free(final);
			g_free(p);
		}
	} else if (combo && combo_new >= 0 && combo_new != combo_start) {
		gtk_combo_box_set_active(GTK_COMBO_BOX(combo), combo_new);
	}

	DBG("Ergebnis: %s (Dateityp %d)\n", uris[0], combo_new);
	g_clear_pointer(&last_save_uri, g_free);
	if (action == GTK_FILE_CHOOSER_ACTION_SAVE)
		last_save_uri = g_strdup(uris[0]);
	g_object_set_data_full(G_OBJECT(dialog), DATA_URIS, uris, (GDestroyNotify)g_strfreev);
	g_object_set_data_full(G_OBJECT(dialog), DATA_CHOICES, ch, (GDestroyNotify)g_hash_table_unref);
	g_variant_unref(res);
	return TRUE;
}

/* ---------- Getter, die das Portal-Ergebnis liefern ---------- */
static char **stored(GtkFileChooser *fc)
{
	return fc ? g_object_get_data(G_OBJECT(fc), DATA_URIS) : NULL;
}

#define REAL(name, type, ...) \
	static type (*real_##name)(__VA_ARGS__); \
	if (!real_##name) real_##name = dlsym(RTLD_NEXT, #name)

char *gtk_file_chooser_get_uri(GtkFileChooser *fc)
{
	char **u = G_IS_OBJECT(fc) ? stored(fc) : NULL;
	if (u)
		return g_strdup(u[0]);
	REAL(gtk_file_chooser_get_uri, char *, GtkFileChooser *);
	char *r = real_gtk_file_chooser_get_uri(fc);
	if (!r && G_IS_OBJECT(fc)) {
		const char *cur = g_object_get_data(G_OBJECT(fc), "fpick-cur");
		if (cur && g_object_get_data(G_OBJECT(fc), "fpick-live"))
			r = g_strdup(cur);       /* nur während go_gtk_file_sel_dialog / Handler-Aufrufen */
	}
	return r;
}

char *gtk_file_chooser_get_filename(GtkFileChooser *fc)
{
	char **u = G_IS_OBJECT(fc) ? stored(fc) : NULL;
	if (u)
		return g_filename_from_uri(u[0], NULL, NULL);
	REAL(gtk_file_chooser_get_filename, char *, GtkFileChooser *);
	return real_gtk_file_chooser_get_filename(fc);
}

GSList *gtk_file_chooser_get_uris(GtkFileChooser *fc)
{
	char **u = G_IS_OBJECT(fc) ? stored(fc) : NULL;
	if (u) {
		GSList *l = NULL;
		for (int i = 0; u[i]; i++)
			l = g_slist_prepend(l, g_strdup(u[i]));
		return g_slist_reverse(l);
	}
	REAL(gtk_file_chooser_get_uris, GSList *, GtkFileChooser *);
	return real_gtk_file_chooser_get_uris(fc);
}

GSList *gtk_file_chooser_get_filenames(GtkFileChooser *fc)
{
	char **u = G_IS_OBJECT(fc) ? stored(fc) : NULL;
	if (u) {
		GSList *l = NULL;
		for (int i = 0; u[i]; i++) {
			char *p = g_filename_from_uri(u[i], NULL, NULL);
			if (p)
				l = g_slist_prepend(l, p);
		}
		return g_slist_reverse(l);
	}
	REAL(gtk_file_chooser_get_filenames, GSList *, GtkFileChooser *);
	return real_gtk_file_chooser_get_filenames(fc);
}

GFile *gtk_file_chooser_get_file(GtkFileChooser *fc)
{
	char **u = G_IS_OBJECT(fc) ? stored(fc) : NULL;
	if (u)
		return g_file_new_for_uri(u[0]);
	REAL(gtk_file_chooser_get_file, GFile *, GtkFileChooser *);
	return real_gtk_file_chooser_get_file(fc);
}

GSList *gtk_file_chooser_get_files(GtkFileChooser *fc)
{
	char **u = G_IS_OBJECT(fc) ? stored(fc) : NULL;
	if (u) {
		GSList *l = NULL;
		for (int i = 0; u[i]; i++)
			l = g_slist_prepend(l, g_file_new_for_uri(u[i]));
		return g_slist_reverse(l);
	}
	REAL(gtk_file_chooser_get_files, GSList *, GtkFileChooser *);
	return real_gtk_file_chooser_get_files(fc);
}

const char *gtk_file_chooser_get_choice(GtkFileChooser *fc, const char *id)
{
	GHashTable *ch = G_IS_OBJECT(fc) ? g_object_get_data(G_OBJECT(fc), DATA_CHOICES) : NULL;
	if (ch) {
		const char *v = g_hash_table_lookup(ch, id);
		if (v)
			return v;
	}
	REAL(gtk_file_chooser_get_choice, const char *, GtkFileChooser *, const char *);
	return real_gtk_file_chooser_get_choice(fc, id);
}
