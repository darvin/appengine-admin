from webob.multidict import UnicodeMultiDict
try:
    from django import newforms as forms
except ImportError:
    from django import forms

class ReferenceSelect(forms.widgets.Select):
    """Customized Select widget that adds link "Add new" near dropdown box.
        This widget should be used for ReferenceProperty support only.
    """
    def __init__(self, urlPrefix = '', referenceKind = '', *attrs, **kwattrs):
        super(ReferenceSelect, self).__init__(*attrs, **kwattrs)
        self.urlPrefix = urlPrefix
        self.referenceKind = referenceKind

    def render(self, *attrs, **kwattrs):
        output = super(ReferenceSelect, self).render(*attrs, **kwattrs)
        return output + u'\n<a href="%s/%s/new/" target="_blank">Add new</a>' % (self.urlPrefix, self.referenceKind)


class FileInput(forms.widgets.Input):
    """Customized FileInput widget that shows downlaod link for uploaded file.
    """
    input_type = 'file'
    needs_multipart_form = True
    download_url_template = '<a href="%(urlPrefix)s/%(modelName)s/get_blob_contents/%(fieldName)s/%(itemKey)s/">File uploaded: %(fileName)s</a>&nbsp;'

    def __init__(self, *args, **kwargs):
        super(FileInput, self).__init__(*args, **kwargs)
        self.urlPrefix = ''
        self.modelName = ''
        self.fieldName = ''
        self.itemKey = ''
        self.fileName = ''
        self.showDownloadLink = False
        self.__args = args
        self.__kwargs = kwargs

    def __copy__(self):
        return FileInput(*self.__args, **self.__kwargs)

    def render(self, name, value, attrs = None):
        """Overrides render() method in order to attach file download
            link if file already uploaded.
        """
        output = super(FileInput, self).render(name, None, attrs=attrs)
        # attach file download link
        if self.showDownloadLink:
            output = self.download_url_template % {
                'urlPrefix': self.urlPrefix,
                'modelName': self.modelName,
                'fieldName': self.fieldName,
                'itemKey': self.itemKey,
                'fileName': self.fileName,
            } + output
        return output

    def value_from_datadict(self, data, name):
        "File widgets take data from FILES, not POST"
        return data.get(name, None)

    def _has_changed(self, initial, data):
        if data is None:
            return False
        return True



### These are taken from Django 1.0 contrib.admin.widgets
class AdminDateWidget(forms.TextInput):
    def __init__(self, attrs={}):
        super(AdminDateWidget, self).__init__(attrs={'class': 'vDateField', 'size': '10'})

class AdminTimeWidget(forms.TextInput):
    def __init__(self, attrs={}):
        super(AdminTimeWidget, self).__init__(attrs={'class': 'vTimeField', 'size': '8'})

class AdminSplitDateTime(forms.SplitDateTimeWidget):
    """
    A SplitDateTime Widget that has some admin-specific styling.
    """
    def __init__(self, attrs=None):
        widgets = [AdminDateWidget, AdminTimeWidget]
        # Note that we're calling MultiWidget, not SplitDateTimeWidget, because
        # we want to define widgets.
        forms.MultiWidget.__init__(self, widgets, attrs)

    def format_output(self, rendered_widgets):
        return u'<p class="datetime">%s %s<br />%s %s</p>' % \
            ('Date:', rendered_widgets[0], 'Time:', rendered_widgets[1])

class SelectMultiple(forms.SelectMultiple):
    def value_from_datadict(self, data, name):
        if isinstance(data, UnicodeMultiDict):
            return data.getall(name)
        return data.get(name, None)