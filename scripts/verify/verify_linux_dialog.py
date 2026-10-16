"""Inspect the native storage error via Linux AT-SPI, outside the product."""
import sys
import pyatspi

def visit(node, depth=0):
    if depth > 12:
        return
    try:
        print(node.name or '')
        for child in node:
            visit(child, depth + 1)
    except (LookupError, RuntimeError):
        pass

for application in pyatspi.Registry.getDesktop(0):
    if application.get_process_id() == int(sys.argv[1]):
        visit(application)
