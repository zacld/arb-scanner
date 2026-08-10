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
  const describe = (el) => {
    const bits = [];
    if (el.id) {
      const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (label) bits.push(label.innerText);
    }
    const wrapping = el.closest('label');
    if (wrapping) bits.push(wrapping.innerText);
    for (const attr of ['aria-label', 'placeholder', 'name', 'title']) {
      const value = el.getAttribute(attr);
      if (value) bits.push(value);
    }
    const described = el.getAttribute('aria-labelledby');
    if (described) {
      for (const id of described.split(/\s+/)) {
        const node = document.getElementById(id);
        if (node) bits.push(node.innerText);
      }
    }
    // Many ATS forms put the question in a sibling/ancestor block, not a <label>.
    const group = el.closest('div,fieldset,li,section');
    if (group) {
      const text = group.innerText || '';
      if (text.length < 600) bits.push(text);
    }
    return [...new Set(bits.map((b) => (b || '').replace(/\s+/g, ' ').trim()).filter(Boolean))];
  };

  const controls = [...document.querySelectorAll('input, select, textarea')];
  const fields = [];
  let counter = 0;
  for (const el of controls) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset'].includes(type)) continue;
    // Rect count, not computed style: a control inside a display:none *ancestor*
    // still reports display:inline on itself, which is exactly how spam honeypots
    // are hidden. Filling one gets the application binned.
    if (el.getClientRects().length === 0) continue;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.opacity === '0') continue;

    const key = 'f' + (counter++);
    el.setAttribute(stampAttr, key);
    const options = el.tagName.toLowerCase() === 'select'
      ? [...el.options].map((o) => ({ value: o.value, label: (o.textContent || '').trim() }))
      : [];
    fields.push({
      key,
      kind: el.tagName.toLowerCase() === 'select' ? 'select'
          : el.tagName.toLowerCase() === 'textarea' ? 'textarea'
          : type,
      name: el.getAttribute('name') || '',
      id: el.id || '',
      required: el.required || el.getAttribute('aria-required') === 'true',
      maxlength: el.getAttribute('maxlength') || '',
      text: describe(el),
      options,
    });
  }
  return fields;
}
"""


@dataclass
class FormField:
    key: str
    kind: str
    name: str = ""
    id: str = ""
    required: bool = False
    maxlength: str = ""
    text: list[str] = field(default_factory=list)
    options: list[dict[str, str]] = field(default_factory=list)
    frame_index: int = 0

    @property
    def selector(self) -> str:
        return f'[{STAMP_ATTR}="{self.key}"]'

    @property
    def label(self) -> str:
        """The shortest descriptive string — usually the actual question."""
        candidates = [t for t in self.text if 2 < len(t) < 200]
        return min(candidates, key=len) if candidates else (self.name or self.id or self.key)

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
