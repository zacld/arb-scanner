"""Reads an arbitrary application form off a live page.

Every ATS renders its own markup, so nothing here assumes Greenhouse (or any
other vendor). We walk whatever inputs the page has, harvest every scrap of text
that hints at what a field wants, and stamp each control with a unique attribute
so it can be located again reliably after the page re-renders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STAMP_ATTR = "data-cvagent-id"

# Runs in the page. Returns one descriptor per fillable control.
_EXTRACT_JS = r"""
(stampAttr) => {
  const clean = (list) =>
    [...new Set(list.map((b) => (b || '').replace(/\s+/g, ' ').trim()).filter(Boolean))];

  // Human-authored labels are kept apart from machine identifiers. Both are
  // useful for matching, but only the former should ever be shown to Zac or
  // used as the question text — "question_30538426" tells a reviewer nothing.
  const describe = (el) => {
    const labels = [];
    if (el.id) {
      const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (label) labels.push(label.innerText);
    }
    const wrapping = el.closest('label');
    if (wrapping) labels.push(wrapping.innerText);
    for (const attr of ['aria-label', 'placeholder', 'title']) {
      const value = el.getAttribute(attr);
      if (value) labels.push(value);
    }
    const described = el.getAttribute('aria-labelledby');
    if (described) {
      for (const id of described.split(/\s+/)) {
        const node = document.getElementById(id);
        if (node) labels.push(node.innerText);
      }
    }
    // Many ATS forms put the question in a sibling/ancestor block, not a <label>.
    let context = '';
    const group = el.closest('div,fieldset,li,section');
    if (group) {
      const text = group.innerText || '';
      if (text.length < 600) context = text;
    }
    return { labels: clean(labels), context: clean([context]) };
  };

  const controls = [...document.querySelectorAll('input, select, textarea')];
  const fields = [];
  let counter = 0;
  for (const el of controls) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset'].includes(type)) continue;
    // File inputs are exempt from every visibility rule below. Greenhouse (and
    // every other modern uploader) hides the real <input type=file> behind a
    // styled "Attach" button, so a visibility filter drops the single most
    // important field on the form. set_input_files works on hidden inputs.
    if (type !== 'file') {
      // Rect count, not computed style: a control inside a display:none
      // *ancestor* still reports display:inline on itself, which is exactly how
      // spam honeypots are hidden. Filling one gets the application binned.
      if (el.getClientRects().length === 0) continue;
      const style = window.getComputedStyle(el);
      if (style.visibility === 'hidden' || style.opacity === '0') continue;
      // Off-screen positioning (left:-9999px) is the other standard honeypot:
      // the element still has a rect, it is just parked outside the viewport.
      const rect = el.getBoundingClientRect();
      if (rect.right <= 0 || rect.bottom <= 0) continue;
    }

    const stamp = 'f' + (counter++);
    el.setAttribute(stampAttr, stamp);
    const described = describe(el);
    const options = el.tagName.toLowerCase() === 'select'
      ? [...el.options].map((o) => ({ value: o.value, label: (o.textContent || '').trim() }))
      : [];
    fields.push({
      stamp,
      kind: el.tagName.toLowerCase() === 'select' ? 'select'
          : el.tagName.toLowerCase() === 'textarea' ? 'textarea'
          : type,
      name: el.getAttribute('name') || '',
      id: el.id || '',
      // Which <form> this control belongs to. A careers page often carries a
      // newsletter signup, a search box or a login form alongside the real
      // application, and those must not be mistaken for application fields.
      form: el.form ? (el.form.id || 'form' + [...document.forms].indexOf(el.form)) : '',
      required: el.required || el.getAttribute('aria-required') === 'true',
      maxlength: el.getAttribute('maxlength') || '',
      // Typeahead widgets (Greenhouse's Location field, most "school" pickers)
      // reject a plain value write — the value must be chosen from the popup or
      // the form treats the field as empty on submit.
      combobox: el.getAttribute('role') === 'combobox'
        || !!el.getAttribute('aria-autocomplete')
        || !!el.getAttribute('aria-controls'),
      labels: described.labels,
      text: [...described.labels, ...described.context],
      options,
    });
  }
  return fields;
}
"""


@dataclass
class FormField:
    stamp: str          # per-frame DOM marker — only unique within its own frame
    kind: str
    name: str = ""
    id: str = ""
    form: str = ""
    required: bool = False
    maxlength: str = ""
    combobox: bool = False
    labels: list[str] = field(default_factory=list)   # human-authored only
    text: list[str] = field(default_factory=list)     # labels + surrounding copy
    options: list[dict[str, str]] = field(default_factory=list)
    frame_index: int = 0

    @property
    def key(self) -> str:
        """Globally unique id. The DOM stamp restarts at f0 in every frame, so an
        embedded Greenhouse form and its host page both produce an 'f0' — keying
        assignments on the bare stamp silently maps one field's value onto
        another's."""
        return f"{self.frame_index}-{self.stamp}"

    @property
    def selector(self) -> str:
        return f'[{STAMP_ATTR}="{self.stamp}"]'

    @property
    def label(self) -> str:
        """The question as a human would read it.

        Drawn from author-written labels only. Machine identifiers are a last
        resort: a review summary listing "question_30538426" instead of "Why do
        you want to work here?" is unreviewable, which defeats the review gate.
        """
        candidates = [t for t in self.labels if 2 < len(t) < 200]
        if candidates:
            return min(candidates, key=len)
        context = [t for t in self.text if 2 < len(t) < 200]
        if context:
            return min(context, key=len)
        return self.name or self.id or self.stamp

    def prompt_view(self) -> dict[str, Any]:
        """The trimmed shape handed to the LLM classifier — full text blobs waste context."""
        view: dict[str, Any] = {
            "key": self.key,
            "kind": self.kind,
            "label": self.label,
            "name": self.name,
            "required": self.required,
        }
        longest = max(self.text, key=len) if self.text else ""
        if longest and longest != self.label:
            view["context"] = longest[:400]
        if self.options:
            view["options"] = [o["label"] for o in self.options if o["label"]][:25]
        if self.maxlength:
            view["maxlength"] = self.maxlength
        return view


_READBACK_JS = r"""
(stampAttr) => {
  const out = {};
  for (const el of document.querySelectorAll('[' + stampAttr + ']')) {
    const key = el.getAttribute(stampAttr);
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (type === 'file') {
      out[key] = el.files && el.files.length ? el.files[0].name : '';
    } else if (type === 'checkbox' || type === 'radio') {
      out[key] = el.checked ? 'checked' : '';
    } else if (el.tagName.toLowerCase() === 'select') {
      const opt = el.selectedOptions[0];
      out[key] = opt ? (opt.textContent || '').trim() : '';
    } else {
      out[key] = el.value || '';
    }
  }
  return out;
}
"""


def read_values(page: Any) -> dict[str, str]:
    """Read back what is actually in each stamped control.

    Writing to a field is not evidence it took. React-controlled inputs can
    revert a programmatic value, a select can silently keep its placeholder, and
    an upload can be rejected by client-side validation — all without raising.
    Reading the DOM back afterwards is the only way to know what an employer
    would actually receive.
    """
    values: dict[str, str] = {}
    for index, frame in enumerate(page.frames):
        try:
            raw = frame.evaluate(_READBACK_JS, STAMP_ATTR)
        except Exception:
            continue
        values.update({f"{index}-{stamp}": value for stamp, value in raw.items()})
    return values


def extract_fields(page: Any) -> list[FormField]:
    """Collect fields from the page and any same-origin iframes.

    Greenhouse and Lever are frequently embedded in a company site via iframe,
    so a top-frame-only scan finds nothing on exactly the pages we care about.
    """
    fields: list[FormField] = []
    for index, frame in enumerate(page.frames):
        try:
            raw = frame.evaluate(_EXTRACT_JS, STAMP_ATTR)
        except Exception:
            continue  # cross-origin frame, or one that navigated mid-scan
        for item in raw:
            fields.append(FormField(frame_index=index, **item))
    return fields


def group_by_form(fields: list[FormField]) -> dict[tuple[int, str], list[FormField]]:
    """Fields grouped by the (frame, form) they belong to."""
    groups: dict[tuple[int, str], list[FormField]] = {}
    for item in fields:
        groups.setdefault((item.frame_index, item.form), []).append(item)
    return groups


def application_form_fields(fields: list[FormField]) -> tuple[list[FormField], list[FormField]]:
    """Split the real application form from unrelated forms on the same page.

    The application is the form carrying the file upload — a newsletter signup or
    search box never has one. Falling back to the largest group keeps behaviour
    sane on the rare application with no attachment field.
    """
    groups = group_by_form(fields)
    if not groups:
        return [], []

    with_upload = [g for g in groups.values() if any(f.kind == "file" for f in g)]
    chosen = max(with_upload or groups.values(), key=len)
    chosen_ids = {id(f) for f in chosen}
    return chosen, [f for f in fields if id(f) not in chosen_ids]
