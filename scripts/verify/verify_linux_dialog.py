"""Inspect the native storage error via Linux AT-SPI, outside the product."""
import sys
import pyatspi

def visit(node, depth=0):
    if depth > 12:
        return
    try:
        print(node.name or '')
        try:
            text = node.queryText()
            print(text.getText(0, text.characterCount))
        except (NotImplementedError, LookupError, RuntimeError):
            pass
        for child in node:
            visit(child, depth + 1)
    except (LookupError, RuntimeError):
        pass

for application in pyatspi.Registry.getDesktop(0):
    pid = application.get_process_id()
    name = application.name or ''
    # GTK/Chromium may expose the dialog under an accessibility utility PID.
    # This Xvfb session contains only this test's CutVoke application.
    if pid == int(sys.argv[1]) or 'cutvoke' in name.lower():
        print(f'Application: {name}; accessibility PID: {pid}; launched PID: {sys.argv[1]}')
        visit(application)
