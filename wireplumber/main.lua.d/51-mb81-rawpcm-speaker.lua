-- MacBook8,1 speakers for WirePlumber 0.4 (Lua); the .conf version only works with 0.5.
table.insert(alsa_monitor.rules, {
  matches = { { { "device.name", "equals", "alsa_card.pci-0000_00_1b.0" } } },
  apply_properties = { ["api.alsa.use-acp"] = false },
})

-- Device 1 (SPDIF) shares a converter with the speakers and clobbers them: hide it.
table.insert(alsa_monitor.rules, {
  matches = { { { "object.path", "equals", "alsa:pcm:PCH:1:playback" } } },
  apply_properties = { ["node.disabled"] = true },
})

-- Device 2 is the 4-channel TDM speaker the EQ chain (MacBook_Speaker_DSP_Out) feeds.
table.insert(alsa_monitor.rules, {
  matches = { { { "object.path", "equals", "alsa:pcm:PCH:2:playback" } } },
  apply_properties = {
    ["node.name"] = "MacBook_Speaker_Raw",
    ["node.description"] = "MacBook Speaker (Raw)",
    ["audio.format"] = "S16LE",
    ["audio.rate"] = 44100,
    ["audio.channels"] = 4,
    ["audio.position"] = "FL,FR,RL,RR",
    ["api.alsa.period-size"] = 1024,
    ["api.alsa.headroom"] = 1024,
    ["node.pause-on-idle"] = false,
    ["session.suspend-timeout-seconds"] = 0,
  },
})
