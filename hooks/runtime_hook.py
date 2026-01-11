"""Runtime hook for Qt theme detection."""

import os
import sys

# On Linux, try to use the native theme
if sys.platform == 'linux':
    # Check for common desktop environments and set appropriate theme
    desktop = os.environ.get('XDG_CURRENT_DESKTOP', '').lower()

    # If no platform theme is set, try to detect one
    if 'QT_QPA_PLATFORMTHEME' not in os.environ:
        if 'gnome' in desktop or 'unity' in desktop or 'budgie' in desktop:
            os.environ['QT_QPA_PLATFORMTHEME'] = 'gnome'
        elif 'kde' in desktop or 'plasma' in desktop:
            os.environ['QT_QPA_PLATFORMTHEME'] = 'kde'
        else:
            # Fallback to gtk3 which works on most Linux desktops
            os.environ['QT_QPA_PLATFORMTHEME'] = 'gtk3'
