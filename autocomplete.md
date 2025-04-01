## Enabling Shell Autocompletion

**Note:** Autocompletion isn’t automatically activated on installation. You must set it up in your shell environment. You have two main approaches:

**Note:** If you used an isolated environment installation with pipx, you need to do a [Manual Shell Configuration](#2-manual-shell-configuration)

### 1. Using activate-global-python-argcomplete

This tool globally enables argcomplete for all your Python CLI tools.

- Run the following command:

    ```bash
    activate-global-python-argcomplete
    ```

- Then, restart your shell.  
  *This registers autocompletion for any tool using argcomplete, including `firestarter`.*

### 2. Manual Shell Configuration

If you prefer to enable autocompletion only for Firestarter, follow these instructions:

#### For Linux and macOS

##### Bash

1. Ensure that the `argcomplete` package is installed (it comes with Firestarter).
2. Add the following line to your `~/.bashrc` file:

    ```bash
    eval "$(register-python-argcomplete firestarter)"
    ```

3. Reload your shell or run the command directly.

##### Zsh

1. Add the same line to your `~/.zshrc` file:

    ```bash
    eval "$(register-python-argcomplete firestarter)"
    ```

2. Restart your terminal session.

#### For Windows (PowerShell)

Argcomplete does not work in the traditional CMD shell. For PowerShell:

1. Open PowerShell as Administrator.
2. Add the following line to your PowerShell profile (usually at `~\Documents\WindowsPowerShell\Microsoft.PowerShell_profile.ps1`):

    ```powershell
    register-python-argcomplete firestarter | Out-String | Invoke-Expression
    ```

3. Restart PowerShell.

*Note:* If you experience issues with autocompletion in Windows, consider using the Windows Subsystem for Linux (WSL) or Git Bash, where the Linux instructions apply.

#### Special Note for pipx Installations

The procedure for enabling autocompletion remains the same whether you install via pip or pipx. The pipx-installed executable is isolated in its own environment, so ensure that the command name (`firestarter`) matches what you reference in your shell configuration. Verify the installation with:

```bash
pipx list
```
