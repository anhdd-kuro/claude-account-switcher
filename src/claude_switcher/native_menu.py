"""Native limits UI adapted from TokenTracker; see resources/TOKEN-TRACKER-NOTICE.md."""

import math
from pathlib import Path

from AppKit import (
    NSAttributedString,
    NSBezierPath,
    NSBox,
    NSBoxCustom,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSFontWeightSemibold,
    NSFontWeightRegular,
    NSForegroundColorAttributeName,
    NSImage,
    NSMakeRect,
    NSNoBorder,
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
    title = NSAttributedString.alloc().initWithString_attributes_(item._menuitem.title(), {
        NSFontAttributeName: NSFont.systemFontOfSize_weight_(NSFont.smallSystemFontSize(), NSFontWeightSemibold),
        NSForegroundColorAttributeName: NSColor.labelColor(),
    })
    item._menuitem.setAttributedTitle_(title)


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
    height = max(1, len(windows)) * 20 + 8
    view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 440, height))
    view.setAccessibilityElement_(False)
    if active:
        background = NSBox.alloc().initWithFrame_(NSMakeRect(22, 3, 406, height - 6))
        background.setBoxType_(NSBoxCustom)
        background.setBorderType_(NSNoBorder)
        background.setCornerRadius_(6)
        background.setFillColor_(NSColor.controlAccentColor().colorWithAlphaComponent_(0.10))
        background.setAccessibilityElement_(False)
        view.addSubview_(background)
    if not windows:
        view.addSubview_(_label(state.display, NSMakeRect(32, 6, 390, 18)))
    for index, window in enumerate(windows):
        label = "7d" if window.label == "7j" else window.label
        y = height - (index + 1) * 20 - 2
        remaining = _remaining(window.percent)
        view.addSubview_(_label(label, NSMakeRect(32, y, 60, 18)))
        bar = LimitBar.alloc().initWithFrame_(NSMakeRect(97, y + 6, 251, 6))
        bar.setAccessibilityElement_(True)
        bar.setAccessibilityRole_("AXProgressIndicator")
        bar.setAccessibilityLabel_(f"{label} remaining")
        bar.setAccessibilityValue_(remaining)
        if window.resets_in:
            bar.setAccessibilityHelp_(f"Resets in {window.resets_in}")
        view.addSubview_(bar)
        view.addSubview_(_label(f"{remaining:.0f}%", NSMakeRect(353, y, 34, 18), align_right=True, digits=True))
        reset = window.resets_in.split()[0] if window.resets_in else ""
        reset_label = _label(reset, NSMakeRect(392, y, 24, 18), align_right=True, digits=True)
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
