"""Phytospectra Desktop — the user-facing app.

Point it at a folder containing NMR / LC-MS data; the analysis runs on
the Phytospectra cloud, and the DOCX report plus zipped report bundle
land in the folder you choose.

Run with ``python -m phytospectra_desktop`` from ``desktop/``.
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import credentials, pipeline
from .auth_desktop import open_token_purchase, sign_in_interactive
from .config import APP_NAME, APP_VERSION

logger = logging.getLogger(__name__)


class DesktopApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title(f"{APP_NAME} {APP_VERSION}")
        root.geometry("680x520")

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Sign in to begin.")
        self.cred: dict | None = credentials.load_credentials()

        self._build()
        if self.cred:
            self.status_var.set(
                f"Signed in as {self.cred.get('email') or self.cred.get('uid')}"
            )

    # ------------------------------------------------------------------ UI
    def _build(self) -> None:
        pad = {"padx": 12, "pady": 6}
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            frame,
            text=("Choose the folder that holds your NMR or LC-MS files.\n"
                  "The analysis runs on the Phytospectra cloud; your report "
                  "is written back to the folder you choose."),
            wraplength=600,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, **pad)

        ttk.Label(frame, text="Data folder:").pack(anchor=tk.W, **pad)
        row = ttk.Frame(frame); row.pack(fill=tk.X, **pad)
        ttk.Entry(row, textvariable=self.input_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text="Browse…", command=self._pick_input).pack(
            side=tk.LEFT, padx=6)

        ttk.Label(frame, text="Output folder (optional):").pack(anchor=tk.W, **pad)
        row2 = ttk.Frame(frame); row2.pack(fill=tk.X, **pad)
        ttk.Entry(row2, textvariable=self.output_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row2, text="Browse…", command=self._pick_output).pack(
            side=tk.LEFT, padx=6)

        buttons = ttk.Frame(frame); buttons.pack(anchor=tk.W, **pad)
        ttk.Button(buttons, text="Run analysis",
                   command=self._run).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Sign in / switch account",
                   command=self._sign_in).pack(side=tk.LEFT, padx=4)
        ttk.Button(buttons, text="Buy tokens",
                   command=open_token_purchase).pack(side=tk.LEFT, padx=4)

        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.pack(fill=tk.X, **pad)

        self.status = tk.Text(frame, height=12, state=tk.DISABLED,
                              font=("Menlo", 10) if _is_mac() else ("Consolas", 9))
        self.status.pack(fill=tk.BOTH, expand=True, **pad)

        ttk.Label(frame, textvariable=self.status_var,
                  foreground="#555").pack(anchor=tk.W, **pad)

    def _pick_input(self) -> None:
        path = filedialog.askdirectory(title="Choose the data folder")
        if path:
            self.input_var.set(path)
            if not self.output_var.get():
                self.output_var.set(path)

    def _pick_output(self) -> None:
        path = filedialog.askdirectory(title="Choose the output folder")
        if path:
            self.output_var.set(path)

    def _log(self, message: str) -> None:
        def go() -> None:
            self.status.configure(state=tk.NORMAL)
            self.status.insert(tk.END, message + "\n")
            self.status.see(tk.END)
            self.status.configure(state=tk.DISABLED)
        self.root.after(0, go)

    # ---------------------------------------------------------------- flows
    def _sign_in(self) -> None:
        def work() -> None:
            try:
                self._log("Opening sign-in…")
                cred = sign_in_interactive(on_message=self._log)
                credentials.save_credentials(cred)
                self.cred = cred
                self.root.after(
                    0, lambda: self.status_var.set(
                        f"Signed in as {cred.get('email') or cred.get('uid')}"))
                self._log("Signed in.")
            except Exception as exc:  # noqa: BLE001 - GUI boundary
                self._log(f"Sign-in failed: {exc}")
        threading.Thread(target=work, daemon=True).start()

    def _token(self) -> str:
        if not self.cred or not self.cred.get("id_token"):
            raise RuntimeError("Not signed in. Use 'Sign in' first.")
        return str(self.cred["id_token"])

    def _run(self) -> None:
        input_dir = self.input_var.get().strip()
        if not input_dir:
            messagebox.showwarning(APP_NAME, "Choose the data folder first.")
            return
        try:
            token = self._token()
        except RuntimeError as exc:
            messagebox.showinfo(APP_NAME, str(exc))
            return

        self.progress.start(12)
        def work() -> None:
            try:
                deliverable = pipeline.run_folder_analysis(
                    input_dir,
                    self.output_var.get().strip() or None,
                    token=token,
                    on_status=self._log,
                )
                self.root.after(0, lambda: messagebox.showinfo(
                    APP_NAME, f"Report ready:\n{deliverable}"))
            except Exception as exc:  # noqa: BLE001 - GUI boundary
                logger.exception("analysis failed")
                self._log(f"FAILED: {exc}")
                message = f"Analysis failed:\n{exc}"
                self.root.after(
                    0, lambda: messagebox.showerror(APP_NAME, message))
            finally:
                self.root.after(0, self.progress.stop)
        threading.Thread(target=work, daemon=True).start()


def _is_mac() -> bool:
    import sys
    return sys.platform == "darwin"


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "aqua" in style.theme_names():
            style.theme_use("aqua")
    except tk.TclError:
        pass
    DesktopApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
