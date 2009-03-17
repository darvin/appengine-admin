"""Settings for appengine_admin
"""
import os.path

# Path to admin template directory
# Overwrite this variable if you want to use custom templates for admin site
ADMIN_TEMPLATE_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'templates')

# Items per page in admin list view
ADMIN_ITEMS_PER_PAGE = 50

# Set by Google - currently 10MB
# This is used for validation of file uploads.
MAX_BLOB_SIZE = 1 * 1024 * 1024

# Suffix for BlobProperty meta info storage.
BLOB_FIELD_META_SUFFIX = '_meta'
