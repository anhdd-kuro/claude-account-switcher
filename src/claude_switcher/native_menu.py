"""Native limits UI adapted from TokenTracker; see resources/TOKEN-TRACKER-NOTICE.md."""

import math
from pathlib import Path

from AppKit import (
    NSBezierPath,
    NSColor,
    NSFont,
    NSFontWeightSemibold,
    NSFontWeightRegular,
    NSImage,
    NSImageView,
    NSMakeRect,
    NSTextAlignmentRight,
    NSTextField,
    NSView,
)

from claude_switcher.auto_switch import account_key
from claude_switcher.usage_state import UsageState


class LimitBar(NSView):
    def drawRect_(self, _):
        bounds = self.bounds()
        radius = bounds.size.height / 2
        NSColor.quaternaryLabelColor().setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, radius, radius).fill()
        remaining = self.accessibilityValue()
        if remaining <= 0:
            return
        color = NSColor.systemRedColor() if remaining <= 10 else (
            NSColor.systemYellowColor() if remaining <= 30 else NSColor.systemGreenColor()
        )
        color.setFill()
        rect = NSMakeRect(0, 0, bounds.size.width * remaining / 100, bounds.size.height)
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, radius, radius).fill()


def style_provider_header(item, provider: str):
    name = provider
    path = Path(__file__).parent / "resources" / f"{name}.svg"
    image = NSImage.alloc().initWithContentsOfFile_(str(path))
    if image:
        image.setSize_((14, 14))
        image.setTemplate_(False)
        item._menuitem.setImage_(image)
    view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 440, 36))
    icon = NSImageView.alloc().initWithFrame_(NSMakeRect(20, 10, 16, 16))
    icon.setImage_(image)
    icon.setAccessibilityElement_(False)
    view.addSubview_(icon)
    title = _label(item._menuitem.title(), NSMakeRect(44, 8, 376, 20), secondary=False)
    title.setFont_(NSFont.systemFontOfSize_weight_(NSFont.systemFontSize(), NSFontWeightSemibold))
    view.addSubview_(title)
    item._menuitem.setView_(view)


def _label(text: str, frame, secondary: bool = True, align_right: bool = False, digits: bool = False):
    field = NSTextField.labelWithString_(text)
    field.setFrame_(frame)
    font = NSFont.monospacedDigitSystemFontOfSize_weight_(NSFont.smallSystemFontSize(), NSFontWeightRegular) if digits else NSFont.menuFontOfSize_(NSFont.smallSystemFontSize())
    field.setFont_(font)
    field.setTextColor_(NSColor.secondaryLabelColor() if secondary else NSColor.labelColor())
    if align_right:
        field.setAlignment_(NSTextAlignmentRight)
    return field


def _remaining(percent: float) -> float:
    return max(0.0, min(100.0, 100 - percent))


def style_usage_item(item, state: UsageState, active: bool = False):
    windows = [window for window in state.windows if math.isfinite(window.percent)] if state.available else []
    height = max(1, len(windows)) * 20 + 16
    view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 440, height))
    view.setAccessibilityElement_(False)
    if not windows:
        view.addSubview_(_label(state.display, NSMakeRect(20, 8, 400, 18)))
    for index, window in enumerate(windows):
        label = "7d" if window.label == "7j" else window.label
        y = height - (index + 1) * 20 - 4
        remaining = _remaining(window.percent)
        view.addSubview_(_label(label, NSMakeRect(20, y, 38, 18)))
        bar = LimitBar.alloc().initWithFrame_(NSMakeRect(62, y + 6, 268, 6))
        bar.setAccessibilityElement_(True)
        bar.setAccessibilityRole_("AXProgressIndicator")
        bar.setAccessibilityLabel_(f"{label} remaining")
        bar.setAccessibilityValue_(remaining)
        if window.resets_in:
            bar.setAccessibilityHelp_(f"Resets in {window.resets_in}")
        view.addSubview_(bar)
        view.addSubview_(_label(f"{remaining:.0f}%", NSMakeRect(338, y, 42, 18), align_right=True, digits=True))
        reset = window.resets_in.split()[0] if window.resets_in else ""
        reset_label = _label(reset, NSMakeRect(388, y, 32, 18), align_right=True, digits=True)
        reset_label.setToolTip_(f"Resets in {window.resets_in}" if window.resets_in else "")
        view.addSubview_(reset_label)
    item._menuitem.setView_(view)


def status_summary(accounts, usage_by_account) -> str:
    parts = []
    for provider in ("claude", "codex"):
        account = next((account for account in accounts if account.provider == provider and account.active), None)
        if not account:
            continue
        state = usage_by_account.get(account_key(account))
        if not state or not state.available or not state.windows:
            continue
        window = state.windows[0]
        if not math.isfinite(window.percent):
            continue
        text = f"{_remaining(window.percent):.0f}%"
        if window.resets_in:
            text += f" · {window.resets_in.split()[0]}"
        parts.append(text)
    return " | ".join(parts)
