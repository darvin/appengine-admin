"""
Tools for dealing with encoding problems.
"""

def smart_str(s, encoding='utf-8', errors='strict'):
    """
    Returns a bytestring version of 's', encoded as specified in 'encoding'.
    This is copied from Django 1.0 django/utils/encoding.py
    """
    if not isinstance(s, basestring):
        try:
            return str(s)
        except UnicodeEncodeError:
            if isinstance(s, Exception):
                # An Exception subclass containing non-ASCII data that doesn't
                # know how to print itself properly. We shouldn't raise a
                # further exception.
                return ' '.join([smart_str(arg, encoding) for arg in s])
            return unicode(s).encode(encoding, errors)
    elif isinstance(s, unicode):
        return s.encode(encoding, errors)
    elif s and encoding != 'utf-8':
        return s.decode('utf-8', errors).encode(encoding, errors)
    else:
        return s

def encoded_str(*args, **kwargs):
    """Decorator for encoding function output in given encoding.
        Available options:
            encoding - name of the encoding. Default: utf-8
            errors - the same as for Python built-in string method encode()
    """
    def decorator(method):
        def wrapper(obj):
            return smart_str(method(obj), *args, **kwargs)
        return wrapper
    return decorator

def encoded_str_utf8(method):
    """Shortcut for decorating __str__ method that returns UTF-8 string.
        Use this decorator like:
        @encoded_str_utf8
        def __str__(self):
            return u"Some text in UTF-8"
        
        This is actually the same as:
        @encoded_str(encoding = 'utf-8')
        def __str__(self):
            return u"Some text in UTF-8"
    """
    def wrapper(obj):
        return smart_str(method(obj), encoding='utf-8')
    return wrapper
