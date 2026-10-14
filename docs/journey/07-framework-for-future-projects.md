# 07. Framework for future projects

A step-by-step method based on this project. Use it for any small Linux tool, especially ones that touch privacy, the firewall, or other apps.

## Phase 1: Goal (5 minutes)
1. Write the goal in **one line**.
2. List what must **never** happen (for this project: "real Chrome must not be touched", "no data may leave after the cut").
3. Answer: is it possible? If only partly, say which part is not.

Output: one line of goal, a short "never" list, a yes/no/partly answer.

## Phase 2: Inventory (10 minutes)
1. What is installed, and how (native package, Flatpak, AppImage)?
2. Versions of the key tools.
3. Where does each app keep its data?

Commands that helped here: `command -v`, `pacman -Qq`, `flatpak list --app`, `flatpak info --show-permissions <id>`, `ls ~/.var/app`.

## Phase 3: Find the mechanism (30 minutes)
1. Write each assumption the plan depends on.
2. Prove each one with a read-only command on the real PC (see 02-research-and-sources.md).
3. Pick the mechanism that does the job with the least power (root only where needed).

Ask yourself:
- Does it work **while running**, or only **at start**?
- Does it touch **only** the target, or other things too?
- What happens when the target starts more processes?

## Phase 4: Smallest working version (commands)
Give the user plain commands first. This proves the mechanism before any GUI is built.

## Phase 5: Small GUI
- One file, standard library plus Tkinter.
- One button per action, a status line, a list that refreshes.
- Settings and app lists in a JSON file, not in code.

## Phase 6: Generalize
- Replace hardcoded names with config (`apps.json`) and with values read from the system.
- Make each feature work for "any app of this kind", then add per-app exceptions only when a test shows they are needed.

## Phase 7: Test each assumption
For each feature, a harmless experiment (see 04-testing.md):
- headless, no network, scratch folder;
- check that real data did not change;
- clean up and check for leftovers.

Ask before anything that needs a password or changes system state.

## Phase 8: Self-review checklist
Go through the code with these questions:

| Question | Example from this project |
|---|---|
| What if the user cancels? | Password cancel showed "Cut" (fixed) |
| What if it fails halfway? | Keep old state unless nft succeeds |
| What if the target starts something new? | New instances went online (fixed with groups) |
| Is there a time gap? | 0.2 s gap at launch (documented, Offline has none) |
| What if two copies of my program run? | Lock file |
| What text reaches root? | Cgroup path validation |
| Who can read my files? | Temp folders set to `700` |
| Does the screen show what is real? | Show `applied`, not `requested` |
| Will it retry forever? | `denied` flag |
| Does every delete point only at my own files? | Paths come from the `profiles/` listing |

## Phase 9: Document
- README with file structure and a guide to each file.
- User guide covering every button.
- Setup guide with checks and expected output for each step.
- Troubleshooting with real error messages from the code.
- Journey: timeline, research, decisions, tests, review, dos and don'ts.
- Write "not recorded" or "not tested" instead of guessing.

## Time savers for next time
- Ask "must it work while the app is running?" in the first message. That one answer changed the whole design here.
- Check `flatpak run --env` style assumptions in a 1-line test before building on them.
- Plan for "the app starts more processes later" from the start.
- Decide the toolkit by the oldest distro you must support, before writing the GUI.
