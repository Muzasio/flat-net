# 06. Dos and don'ts

Learned from this project.

## Do

### Planning
- **Do** restate the goal in one line and say if it is possible before writing code. In this project the user's first request ("cut on launch") was not the real goal ("cut while running").
- **Do** say the limits of each option early (for example "Offline cannot be switched back without restart"). The user can then choose.
- **Do** ask what the user wants when there are clear trade-offs (zero leak vs switchable), and give a recommendation.

### Research
- **Do** check every assumption on the real PC with read-only commands (`--help`, `flatpak info`, `/proc/<pid>/cgroup`, version commands).
- **Do** record exact versions (Flatpak 1.18.4, nftables 1.1.7, Python 3.14.7).
- **Do** look at how an app really runs (`/proc/<pid>/cmdline`, which instance holds which process). This found the Chromium helper sandboxes.

### Code
- **Do** keep app-specific details in a config file (`apps.json`) and read what you can from the system (`flatpak info -m`).
- **Do** rebuild state from scratch (whole firewall table) instead of patching it piece by piece.
- **Do** only show a status after the system confirms it.
- **Do** validate any text that goes into a command run as root.
- **Do** make a privacy block cover everything the app may start later (groups), not only what exists now.
- **Do** stop automatic retries after the user cancels a password prompt.
- **Do** allow only one copy of a program that owns shared state.

### Testing
- **Do** test with headless runs, no network (`--unshare=network`) and scratch folders.
- **Do** check that the real folders did not change (`find -newer marker`).
- **Do** clean up test leftovers, and check a process's command line before stopping it.
- **Do** ask before tests that show password prompts or change the firewall.

## Don't

- **Don't** assume a flag works because it should. `flatpak run --env=XDG_CONFIG_HOME` looked right and did nothing.
- **Don't** assume all apps follow the same rules. Firefox and LibreOffice each needed a different approach.
- **Don't** use `flatpak kill <id>` to close one copy; it closes every copy of that app, including the user's real one. Use `systemctl --user stop <scope>`.
- **Don't** run `rm -rf` on a path you did not check first (the program checks the path came from its own `profiles/` listing).
- **Don't** show "Cut" or "Offline" unless the firewall accepted it.
- **Don't** ask for a password every few seconds. Use one long-running helper.
- **Don't** claim something works if it was not tested. Say "not tested" (the live firewall test in this project, until Cut was tested by hand).
- **Don't** pick a big GUI toolkit if the program must run on old distros. Tkinter is enough for buttons and tables.
- **Don't** leave old versions lying around. `chrome-temp` was removed once FlatNet replaced it.
