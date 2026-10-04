"""A tiny stand-in for Streamlit so the screens can be driven in tests without a browser.

It mimics the parts of Streamlit the portal uses: session state that survives reruns, widgets that read their
value from session state, buttons that are true only for the run right after a click, `on_click` callbacks, and
`st.rerun()` restarting the script.
"""
import contextlib
import importlib
import sys
import types


class RerunSignal(Exception):
    pass


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Progress:
    def __init__(self, log):
        self.log = log

    def progress(self, value, text=None):
        self.log.append((value, text))

    def empty(self):
        pass


class FakeStreamlit(types.ModuleType):
    def __init__(self):
        super().__init__("streamlit")
        self.session_state = {}
        self.pressed = set()
        self.messages = []       # (kind, text)
        self.dataframes = []     # DataFrames passed to st.dataframe
        self.downloads = {}      # key -> (label, data, file_name)
        self.buttons = {}        # key -> disabled?
        self.progress_log = []
        self.sidebar = _Ctx()

    # ---- text output
    def _say(self, kind, text=""):
        self.messages.append((kind, str(text)))

    def set_page_config(self, **kw): pass
    def title(self, t): self._say("title", t)
    def header(self, t): self._say("header", t)
    def subheader(self, t): self._say("subheader", t)
    def caption(self, t): self._say("caption", t)
    def markdown(self, t, **kw): self._say("markdown", t)
    def write(self, t): self._say("write", t)
    def text(self, t): self._say("text", t)
    def info(self, t): self._say("info", t)
    def warning(self, t): self._say("warning", t)
    def error(self, t): self._say("error", t)
    def success(self, t): self._say("success", t)
    def metric(self, label, value): self._say("metric", f"{label}={value}")
    def dataframe(self, df, **kw): self.dataframes.append(df)

    def texts(self, kind):
        return [t for k, t in self.messages if k == kind]

    # ---- layout
    def expander(self, label, expanded=False): return _Ctx()
    def spinner(self, text=""): return _Ctx()
    def columns(self, spec):
        n = spec if isinstance(spec, int) else len(spec)
        return [_Ctx() for _ in range(n)]

    def progress(self, value, text=None):
        self.progress_log.append((value, text))
        return _Progress(self.progress_log)

    # ---- widgets (value lives in session_state under the widget's key)
    def text_area(self, label, key=None, **kw):
        return self.session_state.setdefault(key, "")

    def text_input(self, label, value="", key=None, **kw):
        return self.session_state.setdefault(key, value)

    def checkbox(self, label, key=None, value=False, **kw):
        return self.session_state.setdefault(key, value)

    def button(self, label, key=None, type=None, on_click=None, args=(), disabled=False, **kw):
        self.buttons[key] = disabled
        if key in self.pressed and not disabled:
            self.pressed.discard(key)
            if on_click:
                on_click(*args)
            return True
        return False

    def download_button(self, label, data=None, file_name=None, mime=None, key=None, **kw):
        self.downloads[key] = (label, data, file_name)
        return False

    # ---- control
    def rerun(self): raise RerunSignal()
    def stop(self): raise RerunSignal()


@contextlib.contextmanager
def fake_streamlit():
    """Install the fake as `streamlit`, import the screens against it, and restore everything afterwards."""
    fake = FakeStreamlit()
    saved = {name: sys.modules.get(name) for name in ("streamlit", "portal.ui.screens")}
    sys.modules["streamlit"] = fake
    sys.modules.pop("portal.ui.screens", None)
    try:
        screens = importlib.import_module("portal.ui.screens")
        yield fake, screens
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


def run_script(fake, script, pressed=(), inputs=None, max_runs=10):
    """Simulate one user action: set typed inputs, press buttons, run the script, follow any st.rerun()."""
    fake.session_state.update(inputs or {})
    fake.pressed = set(pressed)
    for _ in range(max_runs):
        fake.messages.clear()
        fake.dataframes.clear()
        fake.buttons.clear()
        try:
            script()
            return
        except RerunSignal:
            fake.pressed = set()   # a button is only "true" on the run right after the click
    raise AssertionError("the script kept rerunning")
