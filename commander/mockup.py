"""
BTCLI Commander — Textual TUI Mockup
Mirrors the design in code.html (dark terminal aesthetic, neon-green accents).
Run with:  python commander/mockup.py
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import (
    Button,
    Checkbox,
    ContentSwitcher,
    DataTable,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Select,
    Static,
)
from textual.containers import Horizontal, VerticalScroll
from rich.text import Text


# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------

MOCK_WALLETS = ["Alice", "Bob", "Charlie"]
MOCK_HOTKEYS: dict[str, list[str]] = {
    "Alice":   ["default", "hotkey1"],
    "Bob":     ["default"],
    "Charlie": ["default"],
}
NETWORKS    = ["finney", "test", "local"]
N_WORDS_OPTS = [12, 15, 18, 21, 24]

WALLET_ROWS = [
    ("Alice",   "1,240.50 TAO", "[SN1: 500.00]  [SN18: 1,200.00]", "1,700.00 TAO"),
    ("Bob",     "  450.12 TAO", "[SN1: 200.12]  [SN11:   250.00]", "  450.12 TAO"),
    ("Charlie", "   89.00 TAO", "[SN3:  89.00]",                   "   89.00 TAO"),
]

LOG_LINES = [
    "[dim][14:22:01][/dim] [green]INFO[/green]  Fetching metadata from chain...",
    "[dim][14:22:03][/dim] [green]INFO[/green]  Alice account synchronized with chain state.",
    "[dim][14:22:03][/dim] [green]INFO[/green]  Bob account synchronized with chain state.",
    "[dim][14:22:03][/dim] [green]INFO[/green]  Charlie account synchronized with chain state.",
    "[dim][14:22:08][/dim] [bright_green]READY[/bright_green] System idling. Waiting for command...",
]

NAV_ENTRIES = [
    ("HOME",      "F1"),
    ("WALLET",    "F2"),
    ("STAKE",     "F3"),
    ("SUDO",      "F4"),
    ("SUBNETS",   "F5"),
    ("PROXY",     "F6"),
    ("CROWDLOAN", "F7"),
    ("BATCH",     "F8"),
]


# ---------------------------------------------------------------------------
# Command field definitions
# ---------------------------------------------------------------------------

@dataclass
class Field:
    id: str
    kind: str   # wallet|hotkey|network|nwords|select|input|path|checkbox
    label: str
    default: Any = None
    choices: list[tuple[str, str]] = dc_field(default_factory=list)
    placeholder: str = ""


def WF(id: str = "wallet_name") -> Field:
    return Field(id, "wallet", "Wallet Name")

def HF(id: str = "wallet_hotkey") -> Field:
    return Field(id, "hotkey", "Hotkey")

def NF(id: str = "network") -> Field:
    return Field(id, "network", "Network", "finney")

def WP() -> Field:
    return Field("wallet_path", "path", "Wallet Path", "~/.bittensor/wallets/")

def CB(id: str, label: str, default: bool = False) -> Field:
    return Field(id, "checkbox", label, default)

def INP(id: str, label: str, default: str = "", ph: str = "") -> Field:
    return Field(id, "input", label, default, placeholder=ph)

def NW() -> Field:
    return Field("n_words", "nwords", "Word Count", "12")

def SL(id: str, label: str, choices: list[tuple[str, str]], default: str | None = None) -> Field:
    return Field(id, "select", label, default, choices)


COMMANDS: dict[str, dict[str, Any]] = {
    "list": {
        "desc": "Display all wallets and their hotkeys.",
        "fields": [WF(), WP(), CB("verbose", "--verbose"), CB("json_output", "--json-output")],
    },
    "overview": {
        "desc": "Display detailed overview of registered accounts on the network.",
        "fields": [
            WF(), WP(), HF(), NF(),
            CB("all_wallets", "--all  (all wallets)"),
            SL("sort_by", "Sort By", [("name","name"),("uid","uid"),("axon","axon")]),
            SL("sort_order", "Sort Order", [("ascending","asc"),("descending","desc")]),
            INP("include_hotkeys", "--include-hotkeys", ph="hotkey names..."),
            INP("exclude_hotkeys", "--exclude-hotkeys", ph="hotkey names..."),
            INP("netuids", "Netuids", ph="1,2,3..."),
            CB("verbose", "--verbose"), CB("json_output", "--json-output"),
        ],
    },
    "transfer": {
        "desc": "Send TAO tokens from one wallet to another.",
        "fields": [
            WF(), WP(), HF(), NF(),
            INP("destination", "Destination SS58", ph="5F..."),
            INP("amount", "Amount (TAO)", ph="0.0"),
            CB("transfer_all", "--all  (transfer entire balance)"),
            CB("allow_death", "--allow-death"),
            INP("period", "Era (blocks)", "16"),
        ],
    },
    "swap-hotkey": {
        "desc": "Swap a hotkey for a registered key pair on the blockchain.",
        "fields": [
            WF(), WP(), HF(), NF(),
            INP("dest_hotkey", "New Hotkey Name", ph="hotkey name..."),
            INP("netuid", "Netuid", ph="0"),
            CB("all_netuids", "--all-netuids"),
        ],
    },
    "inspect": {
        "desc": "Display details of wallet pairs (coldkey, hotkey) on the network.",
        "fields": [
            WF(), WP(), HF(), NF(),
            CB("all_wallets", "--all  (all wallets)"),
            INP("netuids", "Netuids", ph="1,2,3..."),
            CB("verbose", "--verbose"), CB("json_output", "--json-output"),
        ],
    },
    "faucet": {
        "desc": "Obtain test TAO tokens via Proof of Work (local chain only).",
        "fields": [
            WF(), WP(), HF(), NF(),
            INP("processors", "CPU Processors", ph="auto"),
            INP("max_successes", "Max Successes", "3"),
            CB("use_cuda", "--cuda  (use GPU)"),
        ],
    },
    "regen-coldkey": {
        "desc": "Regenerate a coldkey from mnemonic, seed, or JSON backup.",
        "fields": [
            WF(), WP(),
            INP("mnemonic", "Mnemonic Phrase", ph="word1 word2 ..."),
            INP("seed", "Seed (hex)", ph="0x..."),
            INP("json_path", "JSON Backup Path", ph="/path/to/backup.json"),
            CB("use_password", "--use-password", True),
            CB("overwrite", "--overwrite"),
        ],
    },
    "regen-coldkeypub": {
        "desc": "Regenerate the public portion of a coldkey.",
        "fields": [
            WF(), WP(),
            INP("public_key_hex", "Public Key (hex)", ph="0x..."),
            INP("ss58_address", "SS58 Address", ph="5F..."),
            CB("overwrite", "--overwrite"),
        ],
    },
    "regen-hotkey": {
        "desc": "Regenerate a hotkey from mnemonic, seed, or JSON backup.",
        "fields": [
            WF(), WP(), HF(),
            INP("mnemonic", "Mnemonic Phrase", ph="word1 word2 ..."),
            INP("seed", "Seed (hex)", ph="0x..."),
            INP("json_path", "JSON Backup Path", ph="/path/to/backup.json"),
            CB("use_password", "--use-password"),
            CB("overwrite", "--overwrite"),
        ],
    },
    "regen-hotkeypub": {
        "desc": "Regenerate the public portion of a hotkey.",
        "fields": [
            WF(), WP(), HF(),
            INP("public_key_hex", "Public Key (hex)", ph="0x..."),
            INP("ss58_address", "SS58 Address", ph="5F..."),
            CB("overwrite", "--overwrite"),
        ],
    },
    "new-hotkey": {
        "desc": "Create a new hotkey for a wallet.",
        "fields": [
            WF(), WP(),
            INP("wallet_hotkey", "New Hotkey Name", ph="default"),
            NW(),
            CB("use_password", "--use-password"),
            CB("overwrite", "--overwrite"),
        ],
    },
    "associate-hotkey": {
        "desc": "Associate a hotkey SS58 address with a coldkey.",
        "fields": [
            WF(), WP(),
            INP("wallet_hotkey", "Hotkey Name or SS58", ph="name or 5F..."),
            NF(),
        ],
    },
    "new-coldkey": {
        "desc": "Create a new coldkey for a wallet.",
        "fields": [
            WF(), WP(), NW(),
            CB("use_password", "--use-password", True),
            CB("overwrite", "--overwrite"),
        ],
    },
    "swap-check": {
        "desc": "Check status of pending coldkey swap announcements.",
        "fields": [
            INP("wallet_ss58", "Wallet Name or SS58", ph="name or 5F..."),
            WP(), NF(),
            CB("show_all", "--all  (show all pending)"),
            CB("verbose", "--verbose"), CB("json_output", "--json-output"),
        ],
    },
    "create": {
        "desc": "Create a complete wallet (coldkey + hotkey).",
        "fields": [
            WF(), WP(),
            INP("wallet_hotkey", "Hotkey Name", ph="default"),
            NW(),
            CB("use_password", "--use-password", True),
            CB("overwrite", "--overwrite"),
        ],
    },
    "balance": {
        "desc": "Check wallet TAO balance (free and staked amounts).",
        "fields": [
            WF(), WP(), HF(), NF(),
            INP("ss58_addresses", "SS58 Addresses", ph="5F..., 5G..."),
            CB("all_balances", "--all  (all wallets)"),
            SL("sort_by", "Sort By",
               [("name","name"),("free","free"),("staked","staked"),("total","total")]),
            CB("verbose", "--verbose"), CB("json_output", "--json-output"),
        ],
    },
    "history": {
        "desc": "Show transfer history for a wallet (currently disabled).",
        "fields": [WF(), WP(), HF(), CB("verbose", "--verbose")],
    },
    "set-identity": {
        "desc": "Create or update on-chain identity (costs 0.1 TAO).",
        "fields": [
            WF(), WP(), HF(), NF(),
            INP("id_name",     "--id-name",    ph="Display Name"),
            INP("web_url",     "--web-url",    ph="https://..."),
            INP("image_url",   "--image-url",  ph="https://..."),
            INP("discord",     "--discord",    ph="username"),
            INP("description", "--description",ph="About..."),
            INP("github_repo", "--github",     ph="https://github.com/..."),
        ],
    },
    "get-identity": {
        "desc": "Display identity details of a coldkey or hotkey.",
        "fields": [
            WF(), WP(), HF(), NF(),
            INP("coldkey_ss58", "Coldkey SS58", ph="5F..."),
            CB("verbose", "--verbose"), CB("json_output", "--json-output"),
        ],
    },
    "sign": {
        "desc": "Sign a message with wallet coldkey or hotkey.",
        "fields": [
            WF(), WP(), HF(),
            CB("use_hotkey", "--use-hotkey  (sign with hotkey instead)"),
            INP("message", "Message", ph="message to sign..."),
        ],
    },
    "verify": {
        "desc": "Verify a message signature using a public key or SS58 address.",
        "fields": [
            INP("message",          "Message",            ph="original message..."),
            INP("signature",        "Signature (hex)",    ph="0x..."),
            INP("public_key_or_ss58","Address/Public Key",ph="5F... or 0x..."),
        ],
    },
    "swap-coldkey": {
        "desc": "Swap coldkey via two-step process (announce → wait 72h → execute).",
        "fields": [
            WF(), WP(), HF(), NF(),
            SL("action", "Action",
               [("announce","announce"),("execute","execute"),("dispute","dispute")]),
            INP("new_wallet_or_ss58", "New Coldkey or SS58", ph="wallet name or 5F..."),
            CB("mev_protection", "--mev-protection  (recommended)", True),
        ],
    },
}


# ---------------------------------------------------------------------------
# Common widgets
# ---------------------------------------------------------------------------

class AppHeader(Static):
    DEFAULT_CSS = """
    AppHeader { height: 1; background: #131313; padding: 0 1; dock: top; }
    """
    def render(self) -> Text:
        t = Text()
        t.append("BTCLI COMMANDER", style="bold bright_green")
        t.append("  │  ", style="dim green")
        t.append("wss://lite.sub.latent.to:443", style="dim")
        t.append("   " * 10)
        t.append("CHAIN: FINNEY-SUBTENSOR", style="dim")
        return t


class SectionHeader(Static):
    DEFAULT_CSS = """
    SectionHeader { height: 1; color: #66FF00; }
    """
    def __init__(self, title: str) -> None:
        super().__init__()
        self._title = title

    def render(self) -> Text:
        t = Text()
        t.append("┌── ", style="dim green")
        t.append(self._title, style="bold bright_green")
        return t


class NavKeys(Static):
    active: reactive[int] = reactive(0)
    DEFAULT_CSS = """
    NavKeys { width: 1fr; height: 1; overflow-x: hidden; }
    """
    def render(self) -> Text:
        t = Text(overflow="fold", no_wrap=True)
        for i, (name, key) in enumerate(NAV_ENTRIES):
            label = f"[{key}] {name}"
            if i == self.active:
                t.append(f" {label} ", style="bold black on #66FF00")
            else:
                t.append(f" {label} ", style="#c6c6c7")
        return t


class NavStatus(Static):
    DEFAULT_CSS = """
    NavStatus { width: auto; height: 1; padding: 0 1; }
    """
    def render(self) -> Text:
        t = Text(no_wrap=True)
        t.append("● ", style="#00e639")
        t.append("ONLINE", style="#c6c6c7")
        t.append("   ")
        t.append("BTCLI v10.0.0", style="dim")
        return t


class AppFooter(Widget):
    DEFAULT_CSS = """
    AppFooter {
        dock: bottom; height: 2; background: #131313;
        border-top: solid #3c4b35; layout: horizontal;
    }
    """
    def compose(self) -> ComposeResult:
        yield NavKeys(id="nav_keys")
        yield NavStatus()

    def set_active(self, index: int) -> None:
        self.query_one("#nav_keys", NavKeys).active = index


# ---------------------------------------------------------------------------
# Home screen
# ---------------------------------------------------------------------------

class SystemLog(Static):
    DEFAULT_CSS = """
    SystemLog {
        height: 8; border: solid #3c4b35; background: #131313;
        padding: 0 1; margin: 0 0 1 0;
    }
    SystemLog RichLog { background: #131313; height: 1fr; }
    """
    def compose(self) -> ComposeResult:
        yield Label("[dim]btcli.log[/dim]", markup=True)
        yield RichLog(highlight=True, markup=True, id="syslog")

    def on_mount(self) -> None:
        log = self.query_one("#syslog", RichLog)
        for line in LOG_LINES:
            log.write(line)
        log.write("[bright_green]_[/bright_green]")


class WalletTable(Static):
    DEFAULT_CSS = """
    WalletTable { height: auto; }
    WalletTable DataTable { height: auto; background: #1b1b1b; }
    """
    def compose(self) -> ComposeResult:
        yield SectionHeader("Wallet Summary")
        yield DataTable(cursor_type="row", id="wallet_table")
        yield Static(
            Text("  [CREATE NEW WALLET]  ", style="bold black on #66FF00", justify="center"),
            id="create_wallet_btn",
        )

    def on_mount(self) -> None:
        table = self.query_one("#wallet_table", DataTable)
        table.add_columns("Wallet Name", "Free Balance", "Subnet Allocation", "Total Staked")
        for name, free, subnets, total in WALLET_ROWS:
            table.add_row(
                Text(f"» {name}", style="white"),
                Text(free, style="bright_green", justify="right"),
                Text(subnets, style="bright_green"),
                Text(total, style="bold bright_green", justify="right"),
            )


class HomeContent(Widget):
    DEFAULT_CSS = """
    HomeContent { height: 1fr; }
    #main_scroll { background: #131313; padding: 1 2; }
    #create_wallet_btn { margin: 1 0 0 0; text-align: center; }
    SystemLog { margin-top: 1; }
    """
    def compose(self) -> ComposeResult:
        with VerticalScroll(id="main_scroll"):
            yield WalletTable()
            yield SystemLog()


# ---------------------------------------------------------------------------
# Wallet screen
# ---------------------------------------------------------------------------

def _make_widget(f: Field) -> Widget:
    """Instantiate the right Textual widget for a Field (no ID — avoids remount conflicts)."""
    if f.kind == "wallet":
        return Select([(w, w) for w in MOCK_WALLETS], value=MOCK_WALLETS[0])
    if f.kind == "hotkey":
        all_hk = sorted({hk for hks in MOCK_HOTKEYS.values() for hk in hks})
        return Select([(h, h) for h in all_hk], value="default")
    if f.kind == "network":
        return Select([(n, n) for n in NETWORKS], value=f.default or "finney")
    if f.kind == "nwords":
        return Select([(str(n), str(n)) for n in N_WORDS_OPTS], value="12")
    if f.kind == "select":
        opts = f.choices or []
        val  = f.default or (opts[0][1] if opts else Select.BLANK)
        return Select(opts, value=val, allow_blank=not bool(opts))
    if f.kind == "checkbox":
        return Checkbox(f.label, value=bool(f.default))
    # input / path
    return Input(
        value=str(f.default) if f.default else "",
        placeholder=f.placeholder or f.label,
    )


class FieldRow(Horizontal):
    """Label + widget pair for the command form."""
    DEFAULT_CSS = """
    FieldRow { height: auto; margin-bottom: 1; }
    FieldRow .field-label {
        width: 22; color: #85967c; padding: 1 1 0 0; text-align: right;
    }
    FieldRow Input  { width: 1fr; }
    FieldRow Select { width: 1fr; }
    FieldRow Checkbox { width: 1fr; margin-left: 22; }
    """
    def __init__(self, f: Field) -> None:
        super().__init__()
        self._field = f

    def compose(self) -> ComposeResult:
        if self._field.kind != "checkbox":
            yield Label(self._field.label, classes="field-label")
        yield _make_widget(self._field)

    def get_value(self) -> Any:
        for child in self.children:
            if isinstance(child, Input):
                return child.value
            if isinstance(child, Select):
                v = child.value
                return "" if v is Select.BLANK else str(v)
            if isinstance(child, Checkbox):
                return child.value
        return None


class CommandForm(Widget):
    """Right panel: dynamic form for the selected wallet subcommand."""
    DEFAULT_CSS = """
    CommandForm { height: auto; padding: 1 2; }
    CommandForm .form-placeholder { color: #3c4b35; padding: 4 0; text-align: center; }
    CommandForm .cmd-desc {
        color: #85967c; margin-bottom: 2; padding-bottom: 1;
        border-bottom: solid #3c4b35;
    }
    CommandForm .run-btn {
        margin-top: 2; width: 100%; background: #66FF00; color: #022100;
        border: none; text-style: bold;
    }
    CommandForm .run-btn:hover { background: #39ff14; }
    """

    _current_cmd: str = ""

    def compose(self) -> ComposeResult:
        yield Static("← Select a command", classes="form-placeholder")

    def load_command(self, cmd_name: str) -> None:
        if cmd_name == self._current_cmd:
            return
        self._current_cmd = cmd_name
        cmd = COMMANDS[cmd_name]
        new_widgets: list[Widget] = [
            SectionHeader(f"wallet {cmd_name}"),
            Static(cmd["desc"], classes="cmd-desc"),
            *[FieldRow(f) for f in cmd["fields"]],
            Button(f"RUN:  btcli wallet {cmd_name}", classes="run-btn"),
        ]
        self.remove_children()
        self.mount(*new_widgets)

    def _collect_values(self) -> dict[str, Any]:
        return {row._field.id: row.get_value() for row in self.query(FieldRow)}

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if "run-btn" not in event.button.classes:
            return
        vals  = self._collect_values()
        parts = ["btcli", "wallet", self._current_cmd]
        for k, v in vals.items():
            flag = f"--{k.replace('_', '-')}"
            if isinstance(v, bool):
                if v:
                    parts.append(flag)
            elif v:
                parts.append(f"{flag} {v}")
        cmd_str = " ".join(parts)
        try:
            log = self.app.query_one("#syslog", RichLog)
            msg = Text()
            msg.append("[ RUN ]", style="dim")
            msg.append("  ")
            msg.append(cmd_str, style="bright_green")
            log.write(msg)
        except Exception:
            pass


class CommandList(Widget):
    """Left panel: scrollable list of all wallet subcommands."""
    DEFAULT_CSS = """
    CommandList { width: 24; border-right: solid #3c4b35; height: 100%; }
    CommandList ListView { background: #131313; height: 1fr; border: none; }
    CommandList ListView > ListItem { background: #131313; padding: 0 1; }
    CommandList ListView > ListItem.--highlight { background: #2a2a2a; }
    CommandList ListView > ListItem Label { color: #c6c6c7; }
    CommandList ListView > ListItem.--highlight Label { color: #66FF00; }
    """
    def compose(self) -> ComposeResult:
        yield SectionHeader("COMMANDS")
        items = [
            ListItem(Label(name), id=f"cmd_{name.replace('-', '_')}")
            for name in COMMANDS
        ]
        yield ListView(*items, id="cmd_list")


class WalletContent(Widget):
    """F2 wallet screen: command list (left) + dynamic form (right)."""
    DEFAULT_CSS = """
    WalletContent { layout: horizontal; height: 1fr; }
    #form_scroll { width: 1fr; height: 100%; }
    """

    def compose(self) -> ComposeResult:
        yield CommandList()
        with VerticalScroll(id="form_scroll"):
            yield CommandForm(id="cmd_form")

    def on_mount(self) -> None:
        # Pre-load the first command so the form isn't blank
        first = next(iter(COMMANDS))
        self.query_one(CommandForm).load_command(first)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            return
        item_id  = event.item.id or ""
        cmd_name = item_id.removeprefix("cmd_").replace("_", "-")
        if cmd_name in COMMANDS:
            self.query_one(CommandForm).load_command(cmd_name)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class CommanderApp(App):
    TITLE = "BTCLI COMMANDER"
    CSS = """
    Screen { background: #131313; color: #c6c6c7; }
    ContentSwitcher { height: 1fr; }

    DataTable { border: solid #3c4b35; background: #1b1b1b; }
    DataTable > .datatable--header { background: #353535; color: #c6c6c7; text-style: none; }
    DataTable > .datatable--cursor { background: #2a2a2a; color: #66FF00; }
    DataTable > .datatable--hover  { background: #2a2a2a; }

    Input  { border: solid #3c4b35; background: #1b1b1b; color: #e2e2e2; }
    Input:focus { border: solid #66FF00; }
    Select { border: solid #3c4b35; background: #1b1b1b; color: #e2e2e2; }
    Select:focus { border: solid #66FF00; }
    Checkbox { color: #c6c6c7; background: #131313; }
    Checkbox:focus { color: #66FF00; }

    Button { background: #353535; color: #66FF00; border: solid #3c4b35; }
    Button:hover { background: #2a2a2a; }
    """

    BINDINGS = [
        Binding("f1", "nav_home",      show=False),
        Binding("f2", "nav_wallet",    show=False),
        Binding("f3", "nav_stake",     show=False),
        Binding("f4", "nav_sudo",      show=False),
        Binding("f5", "nav_subnets",   show=False),
        Binding("f6", "nav_proxy",     show=False),
        Binding("f7", "nav_crowdloan", show=False),
        Binding("f8", "nav_batch",     show=False),
        Binding("q", "quit", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield AppHeader()
        with ContentSwitcher(initial="home"):
            yield HomeContent(id="home")
            yield WalletContent(id="wallet")
        yield AppFooter(id="app_footer")

    def _nav(self, index: int, content_id: str) -> None:
        self.query_one(AppFooter).set_active(index)
        switcher = self.query_one(ContentSwitcher)
        if content_id in ("home", "wallet"):
            switcher.current = content_id

    def action_nav_home(self)      -> None: self._nav(0, "home")
    def action_nav_wallet(self)    -> None: self._nav(1, "wallet")
    def action_nav_stake(self)     -> None: self._nav(2, "stake")
    def action_nav_sudo(self)      -> None: self._nav(3, "sudo")
    def action_nav_subnets(self)   -> None: self._nav(4, "subnets")
    def action_nav_proxy(self)     -> None: self._nav(5, "proxy")
    def action_nav_crowdloan(self) -> None: self._nav(6, "crowdloan")
    def action_nav_batch(self)     -> None: self._nav(7, "batch")


if __name__ == "__main__":
    CommanderApp().run()
