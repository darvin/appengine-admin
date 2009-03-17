import logging
import pickle
import copy
import datetime

from google.appengine.ext.db import djangoforms
from google.appengine.api import datastore_errors
from google.appengine.ext import db
try:
    from django import newforms as forms
    from django.newforms.util import ValidationError
except ImportError:
    from django import forms
    from django.forms.util import ValidationError

from . import admin_widgets
from . import utils
from . import admin_settings

MAX_BLOB_SIZE = admin_settings.MAX_BLOB_SIZE
BLOB_FIELD_META_SUFFIX = admin_settings.BLOB_FIELD_META_SUFFIX

class AdminModelForm(djangoforms.ModelForm):
    """This class extends ModelForm to be able to pass additional attributes
        to the form while processing the request.
    """
    enctype = ''
    def __init__(self, urlPrefix = '', *args, **kwargs):
        super(AdminModelForm, self).__init__(*args, **kwargs)
        self.urlPrefix = urlPrefix
        instance = kwargs.get('instance', None)

        for fieldName, field in self.fields.items():
            # expose urlPrefix to Select widget
            if isinstance(field.widget, admin_widgets.ReferenceSelect):
                field.widget.urlPrefix = self.urlPrefix
            # deliver meta info to FileInput widget for file download link display
            # do it only if file is uploaded :)
            if instance and isinstance(field.widget, admin_widgets.FileInput) and getattr(instance, fieldName):
                meta = utils.getBlobProperties(instance, fieldName)
                if meta:
                    fileName = meta['File_Name']
                else:
                    fileName = ''
                # these settings should be indivudual for every instance
                field = copy.copy(field)
                widget = copy.copy(field.widget)
                field.widget = widget
                self.fields[fieldName] = field
                # set uploaded file meta data
                widget.showDownloadLink = True
                widget.urlPrefix = self.urlPrefix
                widget.modelName = instance.kind()
                widget.fieldName = fieldName
                widget.itemKey = instance.key()
                widget.fileName = fileName

    def save(self, *args, **kwargs):
        """The overrided method adds uploaded file meta info for BlobProperty fields.
        """
        item = super(AdminModelForm, self).save(*args, **kwargs)
        for fieldName, field in self.fields.items():
            if isinstance(field, FileField) and field.file_name is not None:
                metaFieldName = fieldName + BLOB_FIELD_META_SUFFIX
                if getattr(self.Meta.model, metaFieldName, None):
                    metaData = {
                        'Content_Type': field.file_type,
                        'File_Name': field.file_name,
                        'File_Size': field.file_size,
                    }
                    logging.info("Caching meta data for BlobProperty: %r" % metaData)
                    setattr(item, metaFieldName, pickle.dumps(metaData))
                else:
                    logging.info(
                        'Cache field "%(metaFieldName)s" for blob property "%(propertyName)s" not found. Add field "%(metaFieldName)s" to model "%(modelName)s" if you want to store meta info about the uploaded file',
                        {'metaFieldName': metaFieldName, 'propertyName' : fieldName, 'modelName': self.Meta.model.kind()}
                    )
        # Save the item in Datastore if not told otherwise.
        if kwargs.get('commit', True):
            item.put()
        return item



def createAdminForm(formModel, editFields, editProps):
    """AdminForm factory
        Input: formModel - model that will be used for ModelForm creation
            editFields - tuple of field names that should be exposed in the form
    """
    class AdminForm(AdminModelForm):
        class Meta:
            model = formModel
            fields = editFields

    # Adjust widgets by widget type
    logging.info("Ajusting widgets for AdminForm")
    for fieldName, field in AdminForm.base_fields.items():
        if isinstance(field.widget, forms.widgets.Textarea):
            logging.info("  Adjusting field: %s; widget: %s" % (fieldName, field.widget.__class__))
            field.widget.attrs.update({'rows': '15', 'cols': '40', 'class': 'adminTextarea'})
        if isinstance(field.widget, forms.widgets.TextInput):
            logging.info("  Adjusting field: %s; widget: %s" % (fieldName, field.widget.__class__))
            field.widget.attrs.update({'class': 'adminTextInput'})
        if isinstance(field, djangoforms.ModelChoiceField):
            logging.info("  Adjusting field: %s; widget: %s" % (fieldName, field.widget.__class__))
            # Use custom widget with link "Add new" near dropdown box
            field.widget = admin_widgets.ReferenceSelect(
                attrs = field.widget.attrs,
                urlPrefix = None,
                referenceKind = getattr(formModel, fieldName).reference_class.kind()
            )
            # Choices must be set after creating the widget because in our case choices
            # is not a list but a wrapeper around query that always fetches fresh data from datastore
            field.widget.choices = field.choices
        if getattr(field.widget, 'needs_multipart_form', False):
            AdminForm.enctype = 'multipart/form-data'

    # Adjust widgets by property type
    for prop in editProps:
        if prop.typeName == 'DateProperty':
            AdminForm.base_fields[prop.name].widget = admin_widgets.AdminDateWidget()
        if prop.typeName == 'TimeProperty':
            AdminForm.base_fields[prop.name].widget = admin_widgets.AdminTimeWidget()
        if prop.typeName == 'DateTimeProperty':
            old = AdminForm.base_fields[prop.name]
            AdminForm.base_fields[prop.name] = SplitDateTimeField(
                required = old.required,
                widget = admin_widgets.AdminSplitDateTime,
                label = old.label,
                initial = old.initial,
                help_text = old.help_text
            )
    return AdminForm


class FileField(forms.fields.Field):
    widget = admin_widgets.FileInput
    error_messages = {
        'invalid': u"No file was submitted. Check the encoding type on the form.",
        'missing': u"No file was submitted.",
        'empty': u"The submitted file is empty.",
        'max_size': u"File size too big (%s bytes). Max size: %s bytes",
    }

    def __init__(self, *args, **kwargs):
        super(FileField, self).__init__(*args, **kwargs)
        self.file_name = None
        self.file_size = None
        self.file_type = None
        self.__args = args
        self.__kwargs = kwargs

    def __copy__(self):
        return FileField(*self.__args, **self.__kwargs)

    def clean(self, data, initial=None):
        super(FileField, self).clean(initial or data)

        if not self.required and data in forms.fields.EMPTY_VALUES:
            return None
        elif not data.value and initial:
            return initial

        # UploadedFile objects should have name and size attributes.
        try:
            self.file_name = data.filename
            self.file_size = len(data.value)
            self.file_type = data.type
            file_content = data.value
        except AttributeError:
            raise ValidationError(self.error_messages['invalid'])

        if not self.file_name:
            raise ValidationError(self.error_messages['invalid'])
        if not self.file_size:
            raise ValidationError(self.error_messages['empty'])
        if self.file_size > MAX_BLOB_SIZE:
            raise ValidationError(self.error_messages['max_size'] % (self.file_size, MAX_BLOB_SIZE))

        return file_content
forms.fields.FileField = FileField
forms.FileField = FileField

### HACK HACK HACK ###
# djangoforms.ReferenceProperty.get_value_for_form() does not catch the error that occurs
# when referenced item is deleted.
# This "monkey patch" fixes the problem.
def _wrapped_get_value_for_form(self, instance):
    """Catch "ReferenceProperty failed to be resolved" error and return None.
    """
    try:
        return _original_get_value_for_form(self, instance)
    except datastore_errors.Error, exc:
        # Error is raised if referenced property is deleted
        # Catch the exception and set value to none
        logging.warning('Error catched while getting item values: %s' % exc)
        return  None

_original_get_value_for_form = djangoforms.ReferenceProperty.get_value_for_form
djangoforms.ReferenceProperty.get_value_for_form = _wrapped_get_value_for_form


class ModelMultipleChoiceField(forms.MultipleChoiceField):
    default_error_messages = {
        'invalid_choice': _(u'Please select a valid choice. '
            u'That choice is not one of the available choices.'),
    }

    def __init__(self, reference_class, query=None, choices=None,
            required=True, widget=admin_widgets.SelectMultiple, label=None, initial=None,
                help_text=None, *args, **kwargs):
        """Constructor.

        Args:
          reference_class: required; the db.Model subclass used in the reference
          query: optional db.Query; default db.Query(reference_class)
          choices: optional explicit list of (value, label) pairs representing
            available choices; defaults to dynamically iterating over the
            query argument (or its default)
          required, widget, label, initial, help_text, *args, **kwargs:
            like for forms.Field.__init__(); widget defaults to forms.SelectMultiple
        """
        assert issubclass(reference_class, db.Model)
        if query is None:
            query = db.Query(reference_class)
        assert isinstance(query, db.Query)
        self.reference_class = reference_class
        self._query = query
        self._choices = choices
        super(ModelMultipleChoiceField, self).__init__(choices, required, widget, label, initial,
                help_text, *args, **kwargs)
        self._update_widget_choices()

    def _update_widget_choices(self):
        """Helper to copy the choices to the widget."""
        self.widget.choices = self.choices


    def _get_query(self):
        """Getter for the query attribute."""
        return self._query

    def _set_query(self, query):
        """Setter for the query attribute.
        As a side effect, the widget's choices are updated.
        """
        self._query = query
        self._update_widget_choices()

    query = property(_get_query, _set_query)

    def _generate_choices(self):
        """Generator yielding (key, label) pairs from the query results.
        """
        for inst in self._query:
            yield (inst.key(), unicode(inst))


    def _get_choices(self):
        """Getter for the choices attribute.

        This is required to return an object that can be iterated over
        multiple times.
        """
        if self._choices is not None:
            return self._choices
        return djangoforms._WrapIter(self._generate_choices)

    def _set_choices(self, choices):
        """Setter for the choices attribute.
                As a side effect, the widget's choices are updated.
        """
        self._choices = choices
        self._update_widget_choices()

    choices = property(_get_choices, _set_choices)

    def clean(self, value):
        """Override Field.clean() to do reference-specific value cleaning.
        """
        value = super(ModelMultipleChoiceField, self).clean(value)
        new_value = []
        for item in value:
            if isinstance(item, basestring):
                item = db.Key(item)
            if isinstance(item, self.reference_class):
                item = item.key()
            if not isinstance(item, db.Key):
                raise db.BadValueError('Value must be a key or of type %s' %
                                       self.reference_class.__name__)
            new_value.append(item)
        return new_value

class SplitDateTimeField(forms.fields.SplitDateTimeField):
    def compress(self, data_list):
        """Checks additionaly if all necessary data is supplied
        """
        if data_list and None not in data_list:
            return datetime.datetime.combine(*data_list)
        return None

class MultipleChoiceField(forms.fields.MultipleChoiceField):
    def __init__(self, choices=(), required=True, widget=admin_widgets.SelectMultiple, label=None, initial=None, help_text=None):
        """Translates choices to Django style: [('key1', 'name1'), ('key2', 'name2')] instead of ['name1', 'name2']
        """
        choices = [(item, item) for item in choices]
        super(MultipleChoiceField, self).__init__(choices, required, widget, label, initial, help_text)
