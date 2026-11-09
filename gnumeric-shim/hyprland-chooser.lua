-- filist as the file dialog: Hyprland does not know the title yet when the static rule "filist-chooser" is
-- evaluated (title is set after map) → float + center once the title is there (main windows stay tiled)
local function filist_chooser_float(w)
    if w and w.class == "de.invincible.Filist" and (w.title or ""):find("^filist · ") and not w.floating then
        hl.dispatch(hl.dsp.window.float({ action = "enable", window = "address:" .. w.address }))
        hl.dispatch(hl.dsp.window.resize({ x = 1000, y = 700, exact = true, window = "address:" .. w.address }))
        hl.dispatch(hl.dsp.window.center({ window = "address:" .. w.address }))
        -- a new window ends the maximize of the program below (misc.on_focus_under_fullscreen = 2): put it back
        for _, o in ipairs(hl.get_windows()) do
            if o.class == "gnumeric" or o.class == "org.gnumeric.gnumeric" or o.class == "ONLYOFFICE" then
                hl.dispatch(hl.dsp.window.fullscreen_state({ internal = 1, client = 1, window = "address:" .. o.address }))
            end
        end
    end
end
hl.on("window.open", function (w)
    hl.timer(function () filist_chooser_float(w) end, { timeout = 80, type = "oneshot" })
    hl.timer(function () filist_chooser_float(w) end, { timeout = 250, type = "oneshot" })
end)
pcall(function () hl.on("window.title", filist_chooser_float) end)
