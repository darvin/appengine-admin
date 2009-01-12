import os.path
import logging
import copy
import re
import math

from google.appengine.ext import db
from google.appengine.api import datastore_errors
from google.appengine.ext.webapp import template
from google.appengine.ext import webapp
from google.appengine.api import datastore_errors

from . import authorized

# Path to admin template directory
# Overwrite this variable if you want to use custom templates
ADMIN_TEMPLATE_DIR = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'templates')

ADMIN_ITEMS_PER_PAGE = 50


class Http404(Exception):
    code = 404

class Http500(Exception):
    code = 500

class BaseRequestHandler(webapp.RequestHandler):
    def handle_exception(self, exception, debug_mode):
        logging.warning("Exception catched: %r" % exception)
        if isinstance(exception, Http404) or isinstance(exception, Http500):
            self.error(exception.code)
            path = os.path.join(ADMIN_TEMPLATE_DIR, str(exception.code) + ".html")
            self.response.out.write(template.render(path, {'errorpage': True}))
        else:
            super(BaseRequestHandler, self).handle_exception(exception, debug_mode)


class ModelAdmin(object):
    """Use this class as base for your model registration to admin site.
        Available settings:
        model - db.model derived class that describes your data model
        listFields - list of field names that should be shown in list view
        editFields - list of field names that that should be used as editable fields in admin interface
        readonlyFields - list of field names that should be used as read-only fields in admin interface
        listGql - GQL statement for record ordering/filtering/whatever_else in list view
    """
    model = None
    listFields = ()
    editFields = ()
    readonlyFields = ()
    listGql = ''

    def __init__(self):
        super(ModelAdmin, self).__init__()
        # Cache model name as string
        self.modelName = str(self.model.kind())
        self._listProperties = []
        self._editProperties = []
        self._readonlyProperties = []
        # extract properties from model by propery names
        self._extractProperties(self.listFields, self._listProperties)
        self._extractProperties(self.editFields, self._editProperties)
        self._extractProperties(self.readonlyFields, self._readonlyProperties)

    def _extractProperties(self, fieldNames, storage):
        for propertyName in fieldNames:
            property = getattr(self.model, propertyName)
            # Add extra attribute to property instance: propertyType.
            # This is used later by appengine_admin
            property.propertyType = property.__class__.__name__
            # Cache referenced class name to avoid BadValueError when rendering model_item_edit.html template.
            # Line like this could cause the exception: field.reference_class.kind
            if property.propertyType == 'ReferenceProperty':
                property.reference_class_kind = property.reference_class.kind()
            storage.append(property)

    def _attachListFields(self, item):
        """Attaches property instances for list fields to given data entry.
            This is used in Admin class view methods.
        """
        item.listProperties = copy.deepcopy(self._listProperties)
        for property in item.listProperties:
            try:
                property.value = getattr(item, property.name)
            except datastore_errors.Error, exc:
                # Error is raised if referenced property is deleted
                # Catch the exception and set value to none
                logging.warning('Error catched in ModelAdmin._attachListFields: %s' % exc)
                property.value = None
        return item


## Admin views ##
class Admin(BaseRequestHandler):
    """Use this class as view in your URL scheme definitions.
        Example:
        ===
        import appengine_admin

        application = webapp.WSGIApplication([
            ...
            # Admin pages
            (r'^(/admin)(.*)$', appengine_admin.Admin),
            ...
            ], debug = settings.DEBUG)
        ===
        Feel free to change "/admin" prefix to any other. Please don't change
        anything else in given regular expression as get() and post() methods
        of Admin class always expect to receive two attributes:
        1) prefix - such as "/admin" that will be used for prefixing all generated admin
            site urls
        2) url - admin site page url (without prefix) that is used for determining what
            action on what model user wants to make.
    """
    def __init__(self):
        logging.info("NEW Admin object created")
        super(Admin, self).__init__()
        # Define and compile regexps for Admin site URL scheme.
        # Every URL will be mapped to appropriate method of this
        # class that handles all requests of particular HTTP message
        # type (GET or POST).
        self.getRegexps = [
            [r'^/?$', self.index_get],
            [r'^/([^/]+)/list/$', self.list_get],
            [r'^/([^/]+)/new/$', self.new_get],
            [r'^/([^/]+)/edit/([^/]+)/$', self.edit_get],
            [r'^/([^/]+)/delete/([^/]+)/$', self.delete_get],
        ]
        self.postRegexps = [
            [r'^/([^/]+)/new/$', self.new_post],
            [r'^/([^/]+)/edit/([^/]+)/$', self.edit_post],
        ]
        self._compileRegexps(self.getRegexps)
        self._compileRegexps(self.postRegexps)
        # Store ordered list of registered data models.
        self.models = _modelRegister.keys()
        self.models.sort()
        # This variable is set by get and port methods and used later
        # for constructing new admin urls.
        self.urlPrefix = ''

    def _compileRegexps(self, regexps):
        """Compiles all regular expressions in regexps list
        """
        for i in range(len(regexps)):
            regexps[i][0] = re.compile(regexps[i][0])

    def get(self, urlPrefix, url):
        """Handle HTTP GET
        """
        self.urlPrefix = urlPrefix
        self._callHandlingMethod(url, self.getRegexps)

    def post(self, urlPrefix, url):
        """Handle HTTP POST
        """
        self.urlPrefix = urlPrefix
        self._callHandlingMethod(url, self.postRegexps)

    def _callHandlingMethod(self, url, regexps):
        """Tries matching given url by searching in list of compiled
            regular expressions. Calls method that has been mapped
            to matched regular expression or raises Http404 exception.
            Url example: /ModelName/edit/kasdkjlkjaldkj/
        """
        for regexp, function in regexps:
            matched = regexp.match(url)
            logging.info("Url: %s" % str(url))
            logging.info("regex: %s" % str(regexp))
            if matched:
                function(*matched.groups())
                return
        # raise http error 404 (not found) if no match
        raise Http404()

    @staticmethod
    def _safeGetItem(model, key):
        """Get record of particular model by key.
            Raise Htt404 if not found or if key is not in correct format
        """
        try:
            item = model.get(key)
        except datastore_errors.BadKeyError:
            raise Http404()
        if item is None:
            raise Http404()
        return item


    @authorized.role("admin")
    def index_get(self):
        """Show admin start page
        """
        path = os.path.join(ADMIN_TEMPLATE_DIR, 'index.html')
        self.response.out.write(template.render(path, {
            'models': self.models,
            'urlPrefix': self.urlPrefix,
        }))

    @authorized.role("admin")
    def list_get(self, modelName):
        """Show list of records for particular model
        """
        modelAdmin = getModelAdmin(modelName)
        path = os.path.join(ADMIN_TEMPLATE_DIR, 'model_item_list.html')
        page = Page(
                modelAdmin = modelAdmin,
                itemsPerPage = ADMIN_ITEMS_PER_PAGE,
                currentPage = self.request.get('page', 1)
            )
        # Get only those items that should be displayed in current page
        items = page.getDataForPage()
        self.response.out.write(template.render(path, {
            'models': self.models,
            'urlPrefix': self.urlPrefix,
            'moduleTitle': modelAdmin.modelName,
            'listProperties': modelAdmin._listProperties,
            'items': map(modelAdmin._attachListFields, items),
            'page': page,
        }))

    @authorized.role("admin")
    def new_get(self, modelName):
        """Show form for creating new record of particular model
        """
        modelAdmin = getModelAdmin(modelName)
        editProperties = copy.deepcopy(modelAdmin._editProperties)
        for property in editProperties:
            if property.propertyType == 'ReferenceProperty':
                property.referencedItems = property.reference_class.all()
        templateValues = {
            'models': self.models,
            'urlPrefix': self.urlPrefix,
            'item' : None,
            'moduleTitle': modelAdmin.modelName,
            'editProperties': editProperties,
            'readonlyProperties': modelAdmin._readonlyProperties,
        }
        path = os.path.join(ADMIN_TEMPLATE_DIR, 'model_item_edit.html')
        self.response.out.write(template.render(path, templateValues))

    @authorized.role("admin")
    def new_post(self, modelName):
        """Create new record of particular model
        """
        modelAdmin = getModelAdmin(modelName)
        attributes = {}
        for property in modelAdmin._editProperties:
            logging.info("Property: %s" % property.name)
            # detect preferred data type of property
            data_type = property.data_type
            logging.info("  Prperty type: %s" % data_type)
            # Since basestring can not be directly instantiated use unicode everywhere
            # Not yet decided what to do if non unicode data received.
            if data_type is basestring:
                if property.propertyType == 'BlobProperty':
                    data_type = str
                else:
                    data_type = unicode
            if issubclass(data_type, db.Model):
                attributes[property.name] = data_type.get(self.request.get(property.name))
            else:
                attributes[property.name] = data_type(self.request.get(property.name))
        item = modelAdmin.model(**attributes)
        item.put()
        self.redirect("%s/%s/edit/%s/" % (self.urlPrefix, modelAdmin.modelName, item.key()))

    @authorized.role("admin")
    def edit_get(self, modelName, key = None):
        """Show for for editing existing record of particular model.
            Raises Http404 if record not found.
        """
        modelAdmin = getModelAdmin(modelName)
        item = self._safeGetItem(modelAdmin.model, key)
        item_values = {}
        editProperties = copy.deepcopy(modelAdmin._editProperties)
        readonlyProperties = copy.deepcopy(modelAdmin._readonlyProperties)
        for i in range(len(editProperties)):
            try:
                itemValue = getattr(item, editProperties[i].name)
            except datastore_errors.Error, exc:
                # Error is raised if referenced property is deleted
                # Catch the exception and set value to none
                logging.warning('Error catched while getting list item values: %s' % exc)
                itemValue = None
            editProperties[i].value = itemValue
            logging.info("%s :: %s" % (editProperties[i].name, editProperties[i].value))
            if editProperties[i].propertyType == 'ReferenceProperty':
                editProperties[i].referencedItems = editProperties[i].reference_class.all()
        for i in range(len(readonlyProperties)):
            itemValue = getattr(item, readonlyProperties[i].name)
            readonlyProperties[i].value = itemValue
            logging.info("%s :: %s" % (readonlyProperties[i].name, readonlyProperties[i].value))

        templateValues = {
            'models': self.models,
            'urlPrefix': self.urlPrefix,
            'item' : item,
            'moduleTitle': modelAdmin.modelName,
            'editProperties': editProperties,
            'readonlyProperties': readonlyProperties,
        }
        path = os.path.join(ADMIN_TEMPLATE_DIR, 'model_item_edit.html')
        self.response.out.write(template.render(path, templateValues))

    @authorized.role("admin")
    def edit_post(self, modelName, key):
        """Save details for already existing record of particular model.
            Raises Http404 if record not found.
        """
        modelAdmin = getModelAdmin(modelName)
        item = self._safeGetItem(modelAdmin.model, key)
        for property in modelAdmin._editProperties:
            # detect preferred data type of property
            data_type = property.data_type
            # Since basestring can not be directly instantiated use unicode everywhere
            # Not yet decided what to do if non unicode data received.
            if data_type is basestring:
                if property.propertyType == 'BlobProperty':
                    data_type = str
                else:
                    data_type = unicode
            if issubclass(data_type, db.Model):
                value = data_type.get(self.request.get(property.name))
            else:
                value = data_type(self.request.get(property.name))
            setattr(item, property.name, value)
        item.put()
        self.redirect("%s/%s/edit/%s/" % (self.urlPrefix, modelAdmin.modelName, item.key()))

    @authorized.role("admin")
    def delete_get(self, modelName, key):
        """Delete record of particular model.
            Raises Http404 if record not found.
        """
        modelAdmin = getModelAdmin(modelName)
        item = self._safeGetItem(modelAdmin.model, key)
        item.delete()
        self.redirect("%s/%s/list/" % (self.urlPrefix, modelAdmin.modelName))


class Page(object):
    def __init__(self, modelAdmin, itemsPerPage = 20, currentPage = 1):
        self.modelAdmin = modelAdmin
        self.model = self.modelAdmin.model
        self.itemsPerPage = int(itemsPerPage)
        self.current = int(currentPage) # comes in as unicode
        self.setPageNumbers()
        logging.info("Paging: Maxpages: %r" % self.maxpages)
        logging.info("Paging: Current: %r" % self.current)

    def setPageNumbers(self):
        nItems = float(self.model.all().count())
        logging.info('Paging: Items per page: %s' % self.itemsPerPage)
        logging.info('Paging: Number of items %s' % int(nItems))
        self.maxpages = int(math.ceil(nItems / float(self.itemsPerPage)))
        if self.maxpages < 1:
            self.maxpages = 1
        # validate current page number
        if self.current > self.maxpages or self.current < 1:
            self.current = 1
        if self.current > 1:
            self.prev = self.current - 1
        else:
            self.prev = None
        if self.current < self.maxpages:
            self.next = self.current + 1
        else:
            self.next = None
        self.first = 1
        self.last = self.maxpages

    def getDataForPage(self):
        offset = int((self.current - 1) * self.itemsPerPage)
        query = self.modelAdmin.listGql + ' LIMIT %i, %i' % (offset, self.itemsPerPage)
        logging.info("Paging: GQL: %s" % query)
        return self.model.gql(query)


# holds model_name -> ModelAdmin_instance mapping.
_modelRegister = {}

def register(*args):
    """Registers ModelAdmin instance for corresponding model.
        Only one ModelAdmin instance per model can be active.
        In case if more ModelAdmin instances with same model are registered
        last registered instance will be the active one.
    """
    for modelAdminClass in args:
        modelAdminInstance = modelAdminClass()
        _modelRegister[modelAdminInstance.modelName] = modelAdminInstance
        logging.info("Registering AdminModel '%s' for model '%s'" % (modelAdminClass.__name__, modelAdminInstance.modelName))

def getModelAdmin(modelName):
    """Get ModelAdmin instance for particular model by model name (string).
        Raises Http404 exception if not found.
        This function is used internally by appengine_admin
    """
    try:
        return _modelRegister[modelName]
    except KeyError:
        raise Http404()
