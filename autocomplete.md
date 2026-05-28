<p align="left"><img src="https://raw.githubusercontent.com/henols/firestarter_app/refs/heads/main/images/firestarter_logo.png" alt="Firestarter EPROM Programmer" width="200"></p>

---
## Enabling Shell Autocompletion

Firestarter ships shell completion via [Click](https://click.palletsprojects.com/en/stable/shell-completion/)'s built-in `_FIRESTARTER_COMPLETE=<shell>_source firestarter` mechanism. No external dependency is needed — Click is already a Firestarter runtime dependency, so completion is available the moment Firestarter is installed.

Completion is opt-in: each shell needs the activation line added to its rc / profile file. Pick the section for your shell below.

### Bash

Add the following line to your `~/.bashrc`:

```bash
eval "$(_FIRESTARTER_COMPLETE=bash_source firestarter)"
```

Then reload your shell, or `source ~/.bashrc`.

### Zsh

Add the following line to your `~/.zshrc`:

```zsh
eval "$(_FIRESTARTER_COMPLETE=zsh_source firestarter)"
```

Then restart your terminal session.

### Fish

Save Click's completion script to fish's per-command completions directory:

```fish
_FIRESTARTER_COMPLETE=fish_source firestarter | source
```

For persistence across shell sessions, write the script to
`~/.config/fish/completions/firestarter.fish`:

```fish
mkdir -p ~/.config/fish/completions
_FIRESTARTER_COMPLETE=fish_source firestarter > ~/.config/fish/completions/firestarter.fish
```

### PowerShell

Add the following line to your PowerShell `$PROFILE`
(typically `~\Documents\PowerShell\Microsoft.PowerShell_profile.ps1`):

```powershell
_FIRESTARTER_COMPLETE=powershell_source firestarter | Out-String | Invoke-Expression
```

Restart PowerShell.

### pipx Installations

The procedure above is the same whether you install Firestarter via `pip` or `pipx`. The pipx-installed executable is isolated in its own environment, so make sure the command name (`firestarter`) matches what you reference in your shell configuration. Verify the installation with:

```bash
pipx list
```

### Migrating from a previous Firestarter

Older Firestarter versions used a different completion library, activated via `eval "$(register-python-argcomplete firestarter)"`. That line no longer works — replace it with the matching `_FIRESTARTER_COMPLETE=<shell>_source firestarter` line for your shell from the sections above.

For more details on Click's completion implementation, see the [Click shell completion documentation](https://click.palletsprojects.com/en/stable/shell-completion/).
