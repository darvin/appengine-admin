"""
Database functionality extensions.
ManyToManyProperty taken from http://django-gae-helpers.googlecode.com/svn/trunk/gaeadapter.py
"""

from google.appengine.ext import db
from google.appengine.api.datastore_errors import BadValueError

from . import admin_forms
from . import admin_widgets

class NotImplementedException(Exception):
    pass

class QueryAdapter(object):
    def __init__(self, model, or_querylist = None):
        self.query = db.Query(model)

    def __iter__(self):
        return iter(self.run())

    def __create_gae_arg(self, arg, value):
        parts = arg.split("__")
        if len(parts) > 1:
            field = "".join(parts[:-1])
            search_type = parts[-1]
            operator= "="
            if search_type == "in":
                operator = "in"
            return ("%s %s"%(field,operator), value)
        else:
            return (arg, value)

    def run(self):
        return self.query.run()
        
    def filter(self, **kwargs):
        args = [self.__create_gae_arg(arg, kwargs[arg]) for arg in kwargs]
        for arg in args:
            self.query.filter(*arg)
        return self

    def exclude(self, **kwargs):
        raise NotImplementedException("exclude method is not implemented")

    def order_by(self, *fields):
        for field in fields:
            self.query = self.query.order(field)
        return self

    def count(self):
        return self.query.count()
    
    def get(self):
        return iter(self.run()).next()

    def __len__(self):
        return self.count()

class OrQueryAdapter(QueryAdapter):
    def __init__(self, component_queries):
        self.query_list = component_queries

    def run(self):
        result_set = set()
        for query in self.query_list:
            result = query.run()
            result_set = result_set.union(result)
        return result_set

    def filter(self, **kwargs):
        args = [self.__create_gae_arg(arg, kwargs[arg]) for arg in kwargs]
        for arg in args:
            for query in self.query_list:
                self.query.filter(*arg)
        return self

    def order_by(self, *fields):
        raise NotImplementedException("order_by is not implemented for OR queries")

    def count(self):
        return len(self.run())
    
class Manager(object):
    def __init__(self):
        self.model = None
        
    def _contribute_to_class(self, model, name):
        self.model = model
        
    def _get_base_set(self):
        return QueryAdapter(self.model)

    def all(self):
        return self._get_base_set()

    def get(self, **kwargs):
        try:
            obj = self.filter(**kwargs).get()
        except StopIteration:
            raise self.model.DoesNotExist("Object does not exist")
        return obj
    
    def filter(self, **kwargs):
        return self._get_base_set().filter(**kwargs)
    
    def exclude(self, **kwargs):
        return self._get_base_set().exclude(**kwargs)

    def order_by(self, *fields):
        return self._get_base_set().order_by(*fields)

    def get_or_create(self, **kwargs):
        try:
            obj = self.get(**kwargs)
            return (obj, False)
        except self.model.DoesNotExist:
            obj = self.model(**kwargs)
            obj.save()
            return (obj, True)
        
class _M2MManager(Manager):
    def __init__(self, model_class, model_instance, property):
        self.model = model_class
        self.model_instance = model_instance
        self.property = property
        
    def _get_base_set(self):
        key_list = getattr(self.model_instance, self.property)
        query_list = []
        for key in key_list:
            query = QueryAdapter(self.model)
            filter_dict = {"_appengine_id =": key.id()}
            query = query.filter(**filter_dict)
            query_list.append(query)
        return OrQueryAdapter(query_list)

    def add(self, obj):
        key_list = getattr(self.model_instance, self.property)
        if obj.key() not in key_list:
            key_list.append(obj.key())
            self.model_instance.put()
    
class _ReverseM2MManager(Manager):
    def __init__(self, model_class, model_instance, property):
        self.model = model_class
        self.model_instance = model_instance
        self.property = property
    
    def _get_base_set(self):
        query = QueryAdapter(self.model)
        filter_dict = {self.property: self.model_instance.key()}
        return query.filter(**filter_dict)

class ManyToManyManager(object):
    def __init__(self, model_class, property, manager_class=_M2MManager):
        self.model_class = model_class
        self.property = property
        self.manager_class = manager_class
        
    def __get__(self, model_instance, model_class):
        return self.manager_class(self.model_class, model_instance, self.property)

class ManyToManyProperty(db.ListProperty):
    def __init__(self, reference_class, **kwargs):
        super(ManyToManyProperty, self).__init__(db.Key, **kwargs)
        self.reference_class = reference_class

    def __property_config__(self, model_class, property_name):
        super(ManyToManyProperty, self).__property_config__(model_class, property_name)
        self.collection_name = "%s_set" % model_class.__name__.lower()
        setattr(self.reference_class, self.collection_name, ManyToManyManager(model_class, property_name, manager_class=_ReverseM2MManager))
        setattr(model_class, property_name[1:], ManyToManyManager(self.reference_class, property_name))
        
    def get_form_field(self, **kwargs):
        defaults = {'form_class': admin_forms.ModelMultipleChoiceField,
                    'reference_class': self.reference_class,
                    'required': False}
        defaults.update(kwargs)
        return super(ManyToManyProperty, self).get_form_field(**defaults)

        
class StringListChoicesProperty(db.StringListProperty):
    """Wraps StringListProperty for using SelectMultiple widget instead of default Textarea
    """
    def validate(self, value):
        """Performs full validation.
            Does customized check for values if choices are defined.
        """
        if self.empty(value):
            if self.required:
                raise BadValueError('Property %s is required' % self.name)
        else:
            # In case of StringListProperty it is necessary that all selected items
            # are between defined choices.
            if self.choices:
                for item in value:
                    if item not in self.choices:
                        raise BadValueError('All selected items for property %s must be between predefined choices %s. Current value: %s' %
                            (self.name, self.choices, value))
        if self.validator is not None:
            self.validator(value)
        if value is not None:
            if not isinstance(value, list):
                raise BadValueError('Property %s must be a list' % self.name)

        value = self.validate_list_contents(value)
        return value
    
    def get_form_field(self, **kwargs):
        """Return a Django form field appropriate for a StringList property.

        This defaults to a Textarea widget with a blank initial value.
        """
        defaults = {'form_class': admin_forms.MultipleChoiceField,
            'choices': self.choices,
            'widget': admin_widgets.SelectMultiple,
        }
        defaults.update(kwargs)
        return super(StringListChoicesProperty, self).get_form_field(**defaults)
    
    def get_value_for_form(self, instance):
        value = super(StringListChoicesProperty, self).get_value_for_form(instance)
        if not value:
            return None
        if isinstance(value, basestring):
            value = value.splitlines()
        return value