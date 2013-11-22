
#.apidoc title: Stock icons definitions

""" A dictionary of "icon-name": ("Human name", image-path or False)

    The image-path can be like the "web_icon" of ir.ui.menu, that is:
        <module>,images/some-pic.png
    Where 'images' can also be 'public_html/some/path' or 'web/static'
"""

icon_definitions= {
    'STOCK_ABOUT': ( 'About', False),
    'STOCK_ADD': ( 'Add', False),
    'STOCK_APPLY': ( 'Apply', False),
    'STOCK_BOLD': ( 'Bold', False),

    'STOCK_CANCEL': ( 'Cancel', False),
    'STOCK_CDROM': ( 'CDROM', False),
    'STOCK_CLEAR': ( 'Clear', False),
    'STOCK_CLOSE': ( 'Close', False),
    'STOCK_COLOR_PICKER': ( 'Color Picker', False),

    'STOCK_CONNECT': ( 'Connect', False),
    'STOCK_CONVERT': ( 'Convert', False),
    'STOCK_COPY': ( 'Copy', False),
    'STOCK_CUT': ( 'Cut', False),
    'STOCK_DELETE': ( 'Delete', False),

    'STOCK_DIALOG_AUTHENTICATION': ('Dialog Authentication', False),
    'STOCK_DIALOG_ERROR': ('Dialog Error', False),
    'STOCK_DIALOG_INFO': ('Dialog Info', False),

    'STOCK_DIALOG_QUESTION': ( 'Dialog Question', False),
    'STOCK_DIALOG_WARNING': ( 'Dialog Warning', False),
    'STOCK_DIRECTORY': ( 'Directory', False),
    'STOCK_DISCONNECT': ( 'Disconnect', False),

    'STOCK_DND': ( 'Dnd', False),
    'STOCK_DND_MULTIPLE': ( 'Dnd Multiple', False),
    'STOCK_EDIT': ( 'Edit', False),
    'STOCK_EXECUTE': ( 'Execute', False),
    'STOCK_FILE': ( 'File', False),

    'STOCK_FIND': ( 'Find', False),
    'STOCK_FIND_AND_REPLACE': ( 'Find And Replace', False),
    'STOCK_FLOPPY': ( 'Floppy', False),
    'STOCK_GOTO_BOTTOM': ( 'Goto Bottom', False),

    'STOCK_GOTO_FIRST': ( 'Goto First', False),
    'STOCK_GOTO_LAST': ( 'Goto Last', False),
    'STOCK_GOTO_TOP': ( 'Goto Top', False),
    'STOCK_GO_BACK': ( 'Go Back', False),

    'STOCK_GO_DOWN': ( 'Go Down', False),
    'STOCK_GO_FORWARD': ( 'Go Forward', False),
    'STOCK_GO_UP': ( 'Go Up', False),
    'STOCK_HARDDISK': ( 'Harddisk', False),

    'STOCK_HELP': ( 'Help', False),
    'STOCK_HOME': ( 'Home', False),
    'STOCK_INDENT': ( 'Indent', False),
    'STOCK_INDEX': ( 'Index', False),
    'STOCK_ITALIC': ( 'Italic', False),

    'STOCK_JUMP_TO': ( 'Jump To', False),
    'STOCK_JUSTIFY_CENTER': ( 'Justify Center', False),
    'STOCK_JUSTIFY_FILL': ( 'Justify Fill', False),

    'STOCK_JUSTIFY_LEFT': ( 'Justify Left', False),
    'STOCK_JUSTIFY_RIGHT': ( 'Justify Right', False),
    'STOCK_MEDIA_FORWARD': ( 'Media Forward', False),

    'STOCK_MEDIA_NEXT': ( 'Media Next', False),
    'STOCK_MEDIA_PAUSE': ( 'Media Pause', False),
    'STOCK_MEDIA_PLAY': ( 'Media Play', False),

    'STOCK_MEDIA_PREVIOUS': ( 'Media Previous', False),
    'STOCK_MEDIA_RECORD': ( 'Media Record', False),
    'STOCK_MEDIA_REWIND': ( 'Media Rewind', False),

    'STOCK_MEDIA_STOP': ( 'Media Stop', False),
    'STOCK_MISSING_IMAGE': ( 'Missing Image', False),
    'STOCK_NETWORK': ( 'Network', False),
    'STOCK_NEW': ( 'New', False),

    'STOCK_NO': ( 'No', False),
    'STOCK_OK': ( 'OK', False),
    'STOCK_OPEN': ( 'Open', False),
    'STOCK_PASTE': ( 'Paste', False),
    'STOCK_PREFERENCES': ( 'Preferences', False),

    'STOCK_PRINT': ( 'Print', False),
    'STOCK_PRINT_PREVIEW': ( 'Print Preview', False),
    'STOCK_PROPERTIES': ( 'Properties', False),
    'STOCK_QUIT': ( 'Quit', False),

    'STOCK_REDO': ( 'Redo', False),
    'STOCK_REFRESH': ( 'Refresh', False),
    'STOCK_REMOVE': ( 'Remove', False),
    'STOCK_REVERT_TO_SAVED': ( 'Revert To_saved', False),

    'STOCK_SAVE': ( 'Save', False),
    'STOCK_SAVE_AS': ( 'Save As', False),
    'STOCK_SELECT_COLOR': ( 'Select Color', False),
    'STOCK_SELECT_FONT': ( 'Select Font', False),

    'STOCK_SORT_ASCENDING': ( 'Sort Ascending', False),
    'STOCK_SORT_DESCENDING': ( 'Sort Descending', False),
    'STOCK_SPELL_CHECK': ( 'Spell Check', False),

    'STOCK_STOP': ( 'Stop', False),
    'STOCK_STRIKETHROUGH': ( 'Strikethrough', False),
    'STOCK_UNDELETE': ( 'Undelete', False),
    'STOCK_UNDERLINE': ( 'Underline', False),

    'STOCK_UNDO': ( 'Undo', False),
    'STOCK_UNINDENT': ( 'Unindent', False),
    'STOCK_YES': ( 'Yes', False),
    'STOCK_ZOOM_100': ( 'Zoom 100', False),

    'STOCK_ZOOM_FIT': ( 'Zoom Fit', False),
    'STOCK_ZOOM_IN': ( 'Zoom In', False),
    'STOCK_ZOOM_OUT': ( 'Zoom Out', False),

    'terp-account': ("Account", False),
    'terp-crm': ("CRM", False),
    'terp-mrp': ("MRP", False),
    'terp-product': ("Product", False),
    'terp-purchase': ("Purchase", False),
    'terp-sale': ("Sale", False),
    'terp-tools': ("Tools", False),
    'terp-administration': ("Administration", False),
    'terp-hr': ("HR", False),
    'terp-partner': ("Partner", False),
    'terp-project': ("Project", False),
    'terp-report': ("Report", False),
    'terp-stock': ("Stock", False),
    'terp-calendar': ("Calendar", False),
    'terp-graph': ("Graph", False),
    'terp-check': ("Check", False),
    'terp-go-month': ("Go Month", False),
    'terp-go-year': ("Go Year", False),
    'terp-go-today': ("Go Today", False),
    'terp-document-new': ("Document New", False),
    'terp-camera_test': ("Camera Test", False),
    'terp-emblem-important': ("Emblem Important", False),
    'terp-gtk-media-pause': ("Gtk Media Pause", False),
    'terp-gtk-stop': ("Gtk Stop", False),
    'terp-gnome-cpu-frequency-applet+': ("Gnome Cpu Frequency Applet+", False),
    'terp-dialog-close': ("Dialog Close", False),
    'terp-gtk-jump-to-rtl': ("Gtk Jump To Rtl", False),
    'terp-gtk-jump-to-ltr': ("Gtk Jump To Ltr", False),
    'terp-accessories-archiver': ("Accessories Archiver", False),
    'terp-stock_align_left_24': ("Stock Align Left 24", False),
    'terp-stock_effects-object-colorize': ("Stock Effects Object Colorize", False),
    'terp-go-home': ("Go Home", False),
    'terp-gtk-go-back-rtl': ("Gtk Go Back Rtl", False),
    'terp-gtk-go-back-ltr': ("Gtk Go Back Ltr", False),
    'terp-personal': ("Personal", False),
    'terp-personal-': ("Personal -", False),
    'terp-personal+': ("Personal +", False),
    'terp-accessories-archiver-minus': ("Accessories Archiver Minus", False),
    'terp-accessories-archiver+': ("Accessories Archiver+", False),
    'terp-stock_symbol-selection': ("Stock Symbol Selection", False),
    'terp-call-start': ("Call Start", False),
    'terp-dolar': ("Dolar", False),
    'terp-face-plain': ("Face Plain", False),
    'terp-folder-blue': ("Folder Blue", False),
    'terp-folder-green': ("Folder Green", False),
    'terp-folder-orange': ("Folder Orange", False),
    'terp-folder-yellow': ("Folder Yellow", False),
    'terp-gdu-smart-failing': ("Gdu Smart Failing", False),
    'terp-go-week': ("Go Week", False),
    'terp-gtk-select-all': ("Gtk Select All", False),
    'terp-locked': ("Locked", False),
    'terp-mail-forward': ("Mail Forward", False),
    'terp-mail-message-new': ("Mail Message New", False),
    'terp-mail-replied': ("Mail Replied", False),
    'terp-rating-rated': ("Rating Rated", False),
    'terp-stage': ("Stage", False),
    'terp-stock_format-scientific': ("Stock Format Scientific", False),
    'terp-dolar_ok!': ("Dolar OK!", False),
    'terp-idea': ("Idea", False),
    'terp-stock_format-default': ("Stock Format Default", False),
    'terp-mail-': ("Mail ", False),
    'terp-mail_delete': ("Mail Delete", False),
}

#eof
