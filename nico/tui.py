"""
Textual TUI for NiCo.

Uses the shared business-logic layer in core.py directly, without HTTP.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    Footer,
    Header,
    Input,
    Label,
    Static,
    Tree,
)

from . import config_manager, importer
from .core import (
    list_config_tree,
    load_and_normalize_config,
    read_config_file,
    validate_user_path,
    write_config_files,
)


class SetupScreen(Screen[None]):
    """Collect the target NixOS config directory."""

    def compose(self) -> ComposeResult:
        saved_dir = getattr(self.app, "nixos_dir", "") or ""
        with Container(id="setup-dialog"):
            yield Static("NiCo TUI", id="setup-title")
            yield Static(
                "Kein gueltiges Konfigurationsverzeichnis geladen. "
                "Bitte ein NiCo-/NixOS-Config-Verzeichnis angeben.",
                id="setup-subtitle",
            )
            yield Label("Konfigurationsverzeichnis")
            yield Input(saved_dir, placeholder="/pfad/zur/nixos-config", id="config-dir")
            with Horizontal(classes="setup-actions"):
                yield Button("Oeffnen", id="open", variant="primary")
                yield Button("Beenden", id="quit")
            yield Static("", id="setup-status")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "quit":
            self.app.exit()
            return
        if event.button.id == "open":
            raw = self.query_one("#config-dir", Input).value.strip()
            self.app.call_later(self.app.open_config_dir, raw)


class MainScreen(Screen[None]):
    """Main TUI workspace with file-aware panel, renderer, and tree."""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._tree_nodes: dict[str, object] = {}
        self._highlight_guard = False

    def compose(self) -> ComposeResult:
        with Container(id="main-layout"):
            yield Static("", id="top-status")
            with Horizontal(id="workspace"):
                with Vertical(id="panel-pane"):
                    yield Static("Panel", classes="pane-title", id="panel-title")
                    yield Static("", id="panel-info", markup=False)
                    with Vertical(id="co-form"):
                        yield Label("Hostname")
                        yield Input(id="co-hostname")
                        yield Label("Benutzer")
                        yield Input(id="co-username")
                        yield Label("State-Version")
                        yield Input(id="co-state-version")
                        yield Label("Zeitzone")
                        yield Input(id="co-timezone")
                        yield Label("Locale")
                        yield Input(id="co-locale")
                        yield Checkbox("Flakes aktiv", id="co-flakes")
                        yield Static(
                            "Aenderungen aus diesem Panel werden ueber 'Datei speichern' "
                            "nach configuration.nix geschrieben.",
                            id="co-form-hint",
                            markup=False,
                        )
                with VerticalScroll(id="render-pane"):
                    yield Static("Keine Datei ausgewaehlt", classes="pane-title", id="file-title")
                    yield Static("", id="file-renderer", markup=False)
                    yield Static("", id="render-status")
                with Vertical(id="tree-pane"):
                    yield Static("Dateibaum", classes="pane-title")
                    yield Tree("Config", id="file-tree")
            with Horizontal(id="action-bar"):
                yield Button("Datei speichern", id="save-file", variant="primary")
                yield Button("Datei neu laden", id="reload-file")
                yield Button("Konfig neu laden", id="reload-config")
                yield Button("Pfad wechseln", id="change-dir")
                yield Button("Beenden", id="quit")

    def on_mount(self) -> None:
        tree = self.query_one("#file-tree", Tree)
        tree.show_root = False
        tree.root.expand()
        self.query_one("#co-form", Vertical).display = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "save-file":
            self.app.call_later(self.app.save_selected_file)
        elif button_id == "reload-file":
            self.app.call_later(self.app.reload_selected_file)
        elif button_id == "reload-config":
            self.app.call_later(self.app.reload_current_config)
        elif button_id == "change-dir":
            self.app.call_later(self.app.show_setup_screen)
        elif button_id == "quit":
            self.app.exit()

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if self._highlight_guard:
            return
        data = event.node.data
        if (
            isinstance(data, dict) and
            data.get("type") == "file" and
            data.get("path") != self.app.selected_path
        ):
            self.app.call_later(self.app.open_file, data["path"])

    def populate_workspace(
        self,
        *,
        tree_data: dict,
        selected_path: str | None,
    ) -> None:
        """Refresh tree and top status from loaded config data."""
        self.query_one("#top-status", Static).update(
            f"Konfigurationsverzeichnis: {self.app.nixos_dir}"
        )
        self._populate_tree(tree_data)
        self.show_panel(
            title="Panel",
            info="Datei waehlen, um dateispezifische Informationen oder Felder zu sehen.",
            co_data=None,
        )

        preferred = selected_path or self._pick_default_file(tree_data.get("tree", []))
        if preferred:
            self.app.call_later(self.app.open_file, preferred)
        else:
            self.show_file(
                path="",
                content="",
                file_type=None,
                writable=False,
                status="Keine sichtbare Datei gefunden.",
            )

    def show_file(
        self,
        *,
        path: str,
        content: str,
        file_type: str | None,
        writable: bool,
        status: str,
    ) -> None:
        """Update renderer and metadata for the selected file."""
        title = path or "Keine Datei ausgewaehlt"
        if file_type:
            title = f"{title} [{file_type}]"
        self.query_one("#file-title", Static).update(title)
        self.query_one("#file-renderer", Static).update(content)

        status_text = status
        if not writable:
            status_text = f"{status_text} Schreibgeschuetzt."
        self.query_one("#render-status", Static).update(status_text)
        self._highlight_path(path)

    def show_panel(self, *, title: str, info: str, co_data: dict | None) -> None:
        """Update the left file-aware panel."""
        self.query_one("#panel-title", Static).update(title)
        self.query_one("#panel-info", Static).update(info)
        co_form = self.query_one("#co-form", Vertical)
        if co_data is None:
            co_form.display = False
            return

        co_form.display = True
        self.query_one("#co-hostname", Input).value = str(co_data.get("hostname", ""))
        self.query_one("#co-username", Input).value = str(co_data.get("username", ""))
        self.query_one("#co-state-version", Input).value = str(co_data.get("state_version", ""))
        self.query_one("#co-timezone", Input).value = str(co_data.get("timezone", ""))
        self.query_one("#co-locale", Input).value = str(co_data.get("locale", ""))
        self.query_one("#co-flakes", Checkbox).value = bool(co_data.get("flakes", False))

    def pull_configuration_form(self) -> dict:
        """Read the editable configuration panel fields."""
        return {
            "hostname": self.query_one("#co-hostname", Input).value.strip(),
            "username": self.query_one("#co-username", Input).value.strip(),
            "state_version": self.query_one("#co-state-version", Input).value.strip(),
            "timezone": self.query_one("#co-timezone", Input).value.strip(),
            "locale": self.query_one("#co-locale", Input).value.strip(),
            "flakes": bool(self.query_one("#co-flakes", Checkbox).value),
        }

    def _populate_tree(self, tree_data: dict) -> None:
        tree = self.query_one("#file-tree", Tree)
        tree.root.remove_children()
        self._tree_nodes = {}
        for entry in tree_data.get("tree", []):
            self._add_tree_entry(tree.root, entry)
        tree.root.expand()

    def _add_tree_entry(self, parent, entry: dict) -> None:
        if entry.get("type") == "dir":
            node = parent.add(f"[{entry['name']}]", data=entry)
            node.expand()
            for child in entry.get("children", []):
                self._add_tree_entry(node, child)
        else:
            label = entry["name"]
            file_type = entry.get("file_type")
            if file_type:
                label = f"{label} ({file_type})"
            node = parent.add_leaf(label, data=entry)
            self._tree_nodes[entry["path"]] = node

    def _highlight_path(self, path: str) -> None:
        node = self._tree_nodes.get(path)
        if node is None:
            return
        parent = node.parent
        while parent is not None:
            parent.expand()
            parent = parent.parent
        tree = self.query_one("#file-tree", Tree)
        self._highlight_guard = True
        try:
            tree.select_node(node)
        finally:
            self._highlight_guard = False

    def _pick_default_file(self, entries: list[dict]) -> str | None:
        preferred = ("configuration.nix", "flake.nix", "home.nix")
        flat: list[str] = []

        def _walk(items: list[dict]) -> None:
            for item in items:
                if item.get("type") == "file":
                    flat.append(item["path"])
                else:
                    _walk(item.get("children", []))

        _walk(entries)
        for path in preferred:
            if path in flat:
                return path
        return flat[0] if flat else None


class NicoTuiApp(App[None]):
    """Workspace-oriented NiCo TUI."""

    CSS = """
    Screen {
        background: $surface;
        color: $text;
    }

    #setup-dialog {
        width: 84;
        max-width: 96;
        border: round $accent;
        padding: 1 2;
        background: $panel;
        align: center middle;
        margin: 4 8;
    }

    #setup-title, .pane-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }

    #setup-subtitle, #setup-status, #render-status, #top-status {
        color: $text-muted;
        margin-bottom: 1;
    }

    #main-layout {
        height: 1fr;
        width: 1fr;
        padding: 1;
    }

    #workspace {
        height: 1fr;
    }

    #panel-pane, #render-pane, #tree-pane {
        border: round $accent;
        background: $panel;
        padding: 1;
        height: 1fr;
    }

    #panel-pane {
        width: 34;
        min-width: 28;
        margin-right: 1;
    }

    #render-pane {
        width: 1fr;
        margin-right: 1;
    }

    #tree-pane {
        width: 28;
        min-width: 24;
    }

    #panel-info, #file-tree, #file-renderer {
        height: 1fr;
    }

    #panel-info, #file-renderer {
        overflow-y: auto;
    }

    #co-form {
        margin-top: 1;
    }

    #co-form Label {
        margin-top: 1;
    }

    #co-form-hint {
        color: $text-muted;
        margin-top: 1;
    }

    #action-bar {
        height: auto;
        margin-top: 1;
    }

    Button {
        margin-right: 1;
    }

    .setup-actions {
        height: auto;
        margin-top: 1;
    }
    """

    BINDINGS = [
        ("ctrl+s", "save_file", "Datei speichern"),
        ("ctrl+r", "reload_config", "Konfig neu laden"),
        ("ctrl+o", "change_dir", "Pfad wechseln"),
        ("ctrl+q", "quit", "Beenden"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.nixos_dir: str = config_manager.get_nixos_config_dir() or ""
        self.config_data: dict = {}
        self.config_tree: dict = {}
        self.config_settings: dict = {}
        self.selected_path: str | None = None
        self.selected_file: dict | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()

    def on_mount(self) -> None:
        self.call_after_refresh(self._load_initial_state)

    def action_save_file(self) -> None:
        if isinstance(self.screen, MainScreen):
            self.save_selected_file()

    def action_reload_config(self) -> None:
        if isinstance(self.screen, MainScreen):
            self.reload_current_config()

    def action_change_dir(self) -> None:
        self.show_setup_screen()

    def show_setup_screen(self) -> None:
        if isinstance(self.screen, SetupScreen):
            return
        self.push_screen(SetupScreen())

    def _load_initial_state(self) -> None:
        if self.nixos_dir and self.open_config_dir(self.nixos_dir, show_setup_on_error=False):
            return
        self.show_setup_screen()

    def open_config_dir(self, raw_path: str, *, show_setup_on_error: bool = True) -> bool:
        path, err = validate_user_path(raw_path)
        if err:
            self._update_setup_status(f"Ungueltiger Pfad: {err}")
            self.notify(f"Ungueltiger Pfad: {err}", severity="error")
            if show_setup_on_error:
                self.show_setup_screen()
            return False

        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            msg = f"Verzeichnis konnte nicht angelegt werden: {exc}"
            self._update_setup_status(msg)
            self.notify(msg, severity="error")
            if show_setup_on_error:
                self.show_setup_screen()
            return False

        self.nixos_dir = str(path)
        config_manager.init_nico_dir(self.nixos_dir)
        config_manager.migrate_nico_json(self.nixos_dir)
        config_manager.save_app_settings({"nixos_config_dir": self.nixos_dir})

        try:
            self.config_data = load_and_normalize_config(self.nixos_dir)
            self.config_tree = list_config_tree(self.nixos_dir)
            self.config_settings = config_manager.load_config_settings(self.nixos_dir)
        except Exception as exc:
            msg = f"Laden fehlgeschlagen: {exc}"
            self._update_setup_status(msg)
            self.notify(msg, severity="error")
            if show_setup_on_error:
                self.show_setup_screen()
            return False

        self._show_main_screen()
        self.notify(f"Konfiguration geladen: {self.nixos_dir}")
        return True

    def reload_current_config(self) -> None:
        if not self.nixos_dir:
            self.show_setup_screen()
            return
        self.open_config_dir(self.nixos_dir)

    def open_file(self, rel_path: str) -> None:
        if not self.nixos_dir or not isinstance(self.screen, MainScreen):
            return

        try:
            file_data = read_config_file(self.nixos_dir, rel_path)
        except FileNotFoundError:
            self.notify("Datei nicht gefunden.", severity="error")
            self.reload_current_config()
            return
        except Exception as exc:
            self.notify(f"Datei konnte nicht gelesen werden: {exc}", severity="error")
            return

        self.selected_path = rel_path
        self.selected_file = file_data
        panel_title, panel_info, panel_data = self._build_panel_for_file(file_data)
        self.screen.show_panel(title=panel_title, info=panel_info, co_data=panel_data)
        self.screen.show_file(
            path=file_data["path"],
            content=file_data["content"],
            file_type=file_data.get("file_type"),
            writable=bool(file_data.get("writable", False)),
            status="Nur Vorschau.",
        )

    def reload_selected_file(self) -> None:
        if self.selected_path:
            self.open_file(self.selected_path)

    def save_selected_file(self) -> None:
        if not self.nixos_dir or not isinstance(self.screen, MainScreen):
            return
        if not self.selected_path or not self.selected_file:
            self.notify("Keine Datei ausgewaehlt.", severity="warning")
            return
        if self.selected_path != "configuration.nix":
            self.notify(
                "Speichern ist aktuell nur fuer das configuration.nix-Panel aktiv.",
                severity="warning",
            )
            return

        try:
            self.config_data.update(self.screen.pull_configuration_form())
            write_config_files(
                self.nixos_dir,
                self.config_data,
                label="tui configuration panel save",
            )
        except Exception as exc:
            self.notify(f"Speichern fehlgeschlagen: {exc}", severity="error")
            return

        self.notify("configuration.nix gespeichert.")
        self.open_config_dir(self.nixos_dir)
        self.open_file(self.selected_path)

    def _show_main_screen(self) -> None:
        if isinstance(self.screen, SetupScreen):
            self.pop_screen()
        if not isinstance(self.screen, MainScreen):
            self.push_screen(MainScreen(id="main-screen"))
        self.call_after_refresh(self._refresh_main_screen)

    def _refresh_main_screen(self) -> None:
        if not isinstance(self.screen, MainScreen):
            return
        self.screen.populate_workspace(
            tree_data=self.config_tree,
            selected_path=self.selected_path,
        )

    def _build_panel_for_file(self, file_data: dict) -> tuple[str, str, dict | None]:
        path = file_data.get("path", "")
        content = file_data.get("content", "")
        file_type = file_data.get("file_type")
        writable = bool(file_data.get("writable", False))

        if path == "configuration.nix" or file_type == "co":
            parsed = importer.parse_config(content)
            packages = parsed.get("packages") or []
            extra_users = parsed.get("extra_users") or []
            return (
                "Panel: configuration.nix",
                "\n".join([
                    f"Pfad: {path}",
                    f"Pakete: {len(packages)}",
                    f"Weitere Benutzer: {len(extra_users)}",
                    "",
                    "Editor-Hinweis:",
                    "Dies ist das erste echte NiCo-Panel fuer configuration.nix.",
                    "Die Mitte zeigt den aktuellen Dateistand nur noch als Vorschau.",
                ]),
                {
                    "hostname": parsed.get("hostname", self.config_data.get("hostname", "")),
                    "username": parsed.get("username", self.config_data.get("username", "")),
                    "state_version": parsed.get("state_version", self.config_data.get("state_version", "")),
                    "timezone": parsed.get("timezone", self.config_data.get("timezone", "")),
                    "locale": parsed.get("locale", self.config_data.get("locale", "")),
                    "flakes": parsed.get("flakes", self.config_data.get("flakes", False)),
                },
            )

        if path == "flake.nix" or file_type == "fl":
            parsed = importer.parse_flake_nix(content)
            hosts = [host.get("name", "-") for host in parsed.get("flake_hosts", [])]
            return (
                "Panel: flake.nix",
                "\n".join([
                    f"Pfad: {path}",
                    f"Beschreibung: {parsed.get('flake_description') or '-'}",
                    f"Nixpkgs-Kanal: {parsed.get('flake_nixpkgs_channel') or '-'}",
                    f"Architektur: {parsed.get('flake_arch') or '-'}",
                    f"Hosts: {', '.join(hosts) if hosts else '-'}",
                    "",
                    "Editor-Hinweis:",
                    "Ein echtes Flake-Panel kommt spaeter. Die Mitte bleibt hier Readonly-Vorschau.",
                ]),
                None,
            )

        if path == "home.nix" or file_type == "hm":
            parsed = importer.parse_home_config(content)
            packages = parsed.get("packages") or []
            return (
                "Panel: home.nix",
                "\n".join([
                    f"Pfad: {path}",
                    f"Benutzer: {parsed.get('username') or '-'}",
                    f"Home-Verzeichnis: {parsed.get('home_dir') or '-'}",
                    f"Shell: {parsed.get('shell') or '-'}",
                    f"Pakete: {len(packages)}",
                    "",
                    "Editor-Hinweis:",
                    "Ein echtes Home-Manager-Panel kommt spaeter. Die Mitte bleibt hier Readonly-Vorschau.",
                ]),
                None,
            )

        line_count = len(content.splitlines())
        suffix = Path(path).suffix or "-"
        return (
            "Panel: Datei",
            "\n".join([
                f"Pfad: {path or '-'}",
                f"Typcode: {file_type or '-'}",
                f"Suffix: {suffix}",
                f"Zeilen: {line_count}",
                f"Schreibbar: {'ja' if writable else 'nein'}",
                "",
                "Editor-Hinweis:",
                "Fuer diese Datei existiert noch kein spezialisiertes Panel.",
                "Die Mitte zeigt den Dateistand nur als Renderer, nicht als Editor.",
            ]),
            None,
        )

    def _update_setup_status(self, message: str) -> None:
        if isinstance(self.screen, SetupScreen):
            self.screen.query_one("#setup-status", Static).update(message)


def run() -> None:
    """Run the NiCo Textual application."""
    NicoTuiApp().run()


if __name__ == "__main__":
    run()
