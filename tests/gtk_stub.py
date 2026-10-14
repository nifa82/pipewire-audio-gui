"""Minimal GTK3 stand-in so the app can be tested without a display.

Fidelity matters here. Two behaviours are modelled strictly because they are
what caught real bugs in the app:

* ``TextBuffer.insert(iter, text, len)`` takes a *length*, not a tag name.
  Passing a tag raises ``TypeError`` - which silently emptied the log view
  while looking like it worked.
* Unknown widget attributes/methods raise ``AttributeError`` instead of being
  swallowed, so calling a method that does not exist fails the test rather
  than passing quietly.

Only the GTK surface the app actually uses is implemented.
"""

import sys
import types

_python_new = object.__new__


class _InitCheckStub:
    """gi.require_version does nothing here."""

    def __call__(self, namespace, version):
        return None


class Widget:
    """Base widget: signals recorded, most setters stored for assertions."""

    def __init__(self, **kwargs):
        self._kwargs = kwargs
        self._handlers = {}
        self._sensitive = True
        self._tooltip = None

    def connect(self, signal, callback, *args):
        self._handlers.setdefault(signal, []).append((callback, args))
        return callback

    def emit(self, signal, *args):
        """Test helper: fire a signal at the connected callbacks."""
        results = []
        for callback, extra in self._handlers.get(signal, []):
            results.append(callback(*args))
        return results

    def set_sensitive(self, value):
        self._sensitive = value

    def get_sensitive(self):
        return self._sensitive

    def set_tooltip_text(self, text):
        self._tooltip = text

    def get_tooltip_text(self):
        return self._tooltip

    # Layout no-ops that still record intent.
    def pack_start(self, child, *args):
        self._children = getattr(self, "_children", [])
        self._children.append(child)

    pack_end = pack_start
    add = pack_start

    def remove(self, child):
        children = getattr(self, "_children", [])
        if child in children:
            children.remove(child)

    def remove_all(self):
        self._children = []

    def append(self, item):
        self._children = getattr(self, "_children", [])
        self._children.append(item)

    def get_children(self):
        return list(getattr(self, "_children", []))

    def show_all(self):
        pass

    def set_border_width(self, n):
        self._border = n

    def set_vexpand(self, v):
        self._vexpand = v

    def set_hexpand(self, v):
        self._hexpand = v

    def set_halign(self, v):
        self._halign = v

    def set_line_wrap(self, v):
        self._wrap = v

    def set_selectable(self, v):
        pass

    def set_policy(self, *args):
        pass

    def set_min_content_height(self, h):
        pass

    def set_default_size(self, width, height):
        self._size = (width, height)

    def set_resizable(self, flag):
        self._resizable = flag

    def get_visible(self):
        return getattr(self, "_visible", False)

    def show(self):
        self._visible = True

    def hide(self):
        self._visible = False

    def present(self):
        self._visible = True

    def set_title(self, title):
        self._title = title

    def append_page(self, page, label=None):
        self._pages = getattr(self, "_pages", [])
        self._pages.append((page, label))

    def popup(self, *args):
        pass

    def set_transient_for(self, parent):
        pass

    def set_modal(self, flag):
        pass

    def set_menu(self, menu):
        self._menu = menu

    def set_status(self, status):
        self._status = status


class Label(Widget):
    def __init__(self, label="", xalign=0.0, yalign=0.0, **kwargs):
        super().__init__(**kwargs)
        self._text = label or ""

    def set_text(self, text):
        self._text = text

    def get_text(self):
        return self._text

    def set_markup(self, markup):
        self._text = markup

    def set_width_chars(self, n):
        pass

    def set_selectable(self, v):
        pass


class Button(Widget):
    def __init__(self, label="", **kwargs):
        super().__init__(**kwargs)
        self._label = label

    def get_label(self):
        return self._label


class CheckButton(Button):
    def __init__(self, label="", **kwargs):
        super().__init__(label=label, **kwargs)
        self._active = False

    def set_active(self, value):
        value = bool(value)
        if value != self._active:
            self._active = value
            self.emit("toggled")

    def get_active(self):
        return self._active


class ComboBoxText(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._items = []
        self._active_id = None

    def append(self, ident, text=None):
        self._items.append((ident, text if text is not None else ident))

    def remove_all(self):
        self._items = []
        self._active_id = None

    def get_items(self):
        return list(self._items)

    def set_active_id(self, ident):
        self._active_id = ident

    def get_active_id(self):
        return self._active_id

    def set_active(self, index):
        if 0 <= index < len(self._items):
            self._active_id = self._items[index][0]

    def get_active(self):
        return self._active_id is not None


class Adjustment(Widget):
    def __init__(self, value=0.0, **kwargs):
        super().__init__(**kwargs)
        self.value = value

    def set_value(self, value):
        self.value = value

    def get_value(self):
        return self.value


class SpinButton(Widget):
    def __init__(self, value=0.0, **kwargs):
        super().__init__(**kwargs)
        self._value = float(value)

    @staticmethod
    def new_with_range(minimum, maximum, step):
        widget = SpinButton(value=minimum)
        widget.range = (minimum, maximum, step)
        return widget

    def set_value(self, value):
        self._value = float(value)

    def get_value(self):
        return self._value

    def set_digits(self, digits):
        pass

    def set_numeric(self, flag):
        pass

    def set_width_chars(self, n):
        pass

    def get_value_as_int(self):
        return int(self._value)


class TextTag:
    def __init__(self, name, **props):
        self.name = name
        self.props = props


class TextBuffer:
    """Faithful about insert()'s third argument being a length."""

    def __init__(self):
        self._tags = {}
        self._chunks = []

    def create_tag(self, name, **props):
        if name in self._tags:
            raise ValueError("tag already exists: %s" % name)
        self._tags[name] = TextTag(name, **props)
        return self._tags[name]

    def get_end_iter(self):
        return types.SimpleNamespace(offset=sum(len(t) for t, _ in self._chunks))

    def insert(self, iter_, text, length=-1):
        if isinstance(length, str):
            # Real PyGObject raises here; the old code passed tag names.
            raise TypeError(
                "insert() third argument must be an int length, got str %r" % length
            )
        self._chunks.append((text, ()))
        return iter_

    def insert_with_tags_by_name(self, iter_, text, length=-1, *tag_names):
        for name in tag_names:
            if name not in self._tags:
                raise ValueError("no such text tag: %s" % name)
        self._chunks.append((text, tuple(tag_names)))
        return iter_

    # --- test helpers -------------------------------------------------
    def get_text(self, include_tags=True):
        return "".join(text for text, _ in self._chunks)

    def tagged_chunks(self):
        return [(text, tags) for text, tags in self._chunks if tags]


class TextView(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._buffer = TextBuffer()

    def get_buffer(self):
        return self._buffer

    def set_editable(self, v):
        pass

    def set_cursor_visible(self, v):
        pass

    def set_wrap_mode(self, v):
        pass

    def scroll_to_iter(self, *args):
        pass


class MenuItem(Widget):
    def __init__(self, label="", **kwargs):
        super().__init__(**kwargs)
        self._label = label

    def get_label(self):
        return self._label


class CheckMenuItem(MenuItem):
    def __init__(self, label="", **kwargs):
        super().__init__(label=label, **kwargs)
        self._active = False

    def set_active(self, value):
        value = bool(value)
        if value != self._active:
            self._active = value
            self.emit("toggled")

    def get_active(self):
        return self._active


class SeparatorMenuItem(MenuItem):
    pass


class StatusIcon(Widget):
    _created = 0

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._tooltip = None

    @staticmethod
    def new_from_icon_name(name):
        StatusIcon._created += 1
        return StatusIcon()

    def set_tooltip_text(self, text):
        self._tooltip = text

    def set_from_icon_name(self, name):
        pass


class Settings:
    def __init__(self):
        self._props = {
            "gtk-theme-name": "Adwaita",
            "gtk-application-prefer-dark-theme": False,
        }

    def get_property(self, name):
        return self._props[name]

    def set_property(self, name, value):
        self._props[name] = value


class CssProvider:
    def __init__(self):
        self.data = None

    def load_from_data(self, data):
        self.data = data


class StyleContext:
    def __init__(self):
        self.providers = []

    def add_provider(self, provider, priority):
        self.providers.append((provider, priority))

    def remove_provider(self, provider):
        self.providers = [p for p in self.providers if p[0] is not provider]

    @staticmethod
    def get_for_screen(screen):
        return _screen_context


class Dialog(Widget):
    def run(self):
        return -6  # GTK_RESPONSE_CANCEL

    def destroy(self):
        pass

    def get_content_area(self):
        return Widget()


class AboutDialog(Dialog):
    def set_program_name(self, name):
        self.program_name = name

    def set_version(self, version):
        self.version = version

    def set_copyright(self, text):
        self.copyright = text

    def set_license_type(self, license_type):
        self.license_type = license_type

    def set_website(self, url):
        self.website = url

    def set_website_label(self, label):
        self.website_label = label

    def set_comments(self, text):
        self.comments = text

    def set_authors(self, authors):
        self.authors = authors


class MessageDialog(Dialog):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.secondary = None

    def format_secondary_text(self, text):
        self.secondary = text


_settings = Settings()
_screen = types.SimpleNamespace()
_screen_context = StyleContext()


def _make_enum(name, *members):
    return types.SimpleNamespace(
        **{member: member for member in members},
        __name__=name,
    )


Gtk = types.SimpleNamespace(
    Window=Widget,
    Box=Widget,
    Label=Label,
    Button=Button,
    CheckButton=CheckButton,
    Frame=Widget,
    ComboBoxText=ComboBoxText,
    Menu=Widget,
    MenuItem=MenuItem,
    CheckMenuItem=CheckMenuItem,
    SeparatorMenuItem=SeparatorMenuItem,
    Notebook=Widget,
    ScrolledWindow=Widget,
    TextView=TextView,
    SpinButton=SpinButton,
    Adjustment=Adjustment,
    StatusIcon=StatusIcon,
    Settings=Settings,
    CssProvider=CssProvider,
    StyleContext=StyleContext,
    Dialog=Dialog,
    AboutDialog=AboutDialog,
    MessageDialog=MessageDialog,
    FileChooserDialog=Dialog,
    FileChooserAction=_make_enum("FileChooserAction", "OPEN", "SAVE"),
    FileFilter=Widget,
    ListStore=Widget,
    TreeView=Widget,
    TreeViewColumn=Widget,
    CellRendererText=Widget,
    Scale=Widget,
    Entry=Widget,
    Orientation=_make_enum("Orientation", "VERTICAL", "HORIZONTAL"),
    PolicyType=_make_enum("PolicyType", "AUTOMATIC", "ALWAYS", "NEVER"),
    WrapMode=_make_enum("WrapMode", "WORD", "CHAR", "NONE"),
    Align=_make_enum("Align", "START", "END", "CENTER"),
    WindowPosition=_make_enum("WindowPosition", "CENTER", "MOUSE"),
    License=_make_enum("License", "MIT_X11", "GPL"),
    ResponseType=_make_enum(
        "ResponseType", "OK", "CANCEL", "CLOSE", "ACCEPT", "DELETE_EVENT"
    ),
    ButtonsType=_make_enum("ButtonsType", "OK_CANCEL", "OK", "CLOSE"),
    MessageType=_make_enum("MessageType", "INFO", "WARNING", "QUESTION", "ERROR"),
    STOCK_CANCEL="cancel",
    STOCK_OK="ok",
    STYLE_PROVIDER_PRIORITY_APPLICATION=600,
    main=lambda: None,
    main_quit=lambda: None,
    init_check=lambda: True,
)

Gdk = types.SimpleNamespace(
    Screen=types.SimpleNamespace(get_default=staticmethod(lambda: _screen))
)

GLib = types.SimpleNamespace(
    timeout_add=lambda interval, func, *a: 1,
    timeout_add_seconds=lambda interval, func, *a: 2,
    idle_add=lambda func, *a: 3,
)


def install():
    """Register the fake gi modules in sys.modules (idempotent)."""
    gi = types.ModuleType("gi")
    gi.require_version = _InitCheckStub()

    repository = types.ModuleType("gi.repository")
    repository.__path__ = []
    repository.Gtk = Gtk
    repository.Gdk = Gdk
    repository.GLib = GLib

    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = repository
    return gi, repository


def settings():
    """The singleton Settings the app will see."""
    return _settings


def style_context():
    return _screen_context