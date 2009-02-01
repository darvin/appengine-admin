import os.path
import logging
import copy
import re
import math
import pickle

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

BLOB_FIELD_META_SUFFIX = '_meta'


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


class PropertyWrapper(object):
    def __init__(self, prop, name):
        logging.info("Caching info about property '%s'" % name)
        self.prop = prop
        self.name = name
        self.typeName = prop.__class__.__name__
        logging.info("  Property type: %s" % self.typeName)
        # Cache referenced class name to avoid BadValueError when rendering model_item_edit.html template.
        # Line like this could cause the exception: field.reference_class.kind
        if self.typeName == 'ReferenceProperty':
            self.reference_kind = prop.reference_class.kind()
        # This might fail in case if prop is instancemethod
        self.verbose_name = getattr(prop, 'verbose_name', self.name)
        # set verbose_name to at least something represenative
        if not self.verbose_name:
            self.verbose_name = self.name
        self.value = ''
    
    def __deepcopy__(self, memo):
        return PropertyWrapper(self.prop, self.name)


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
            storage.append(PropertyWrapper(getattr(self.model, propertyName), propertyName))

    def _attachListFields(self, item):
        """Attaches property instances for list fields to given data entry.
            This is used in Admin class view methods.
        """
        item.listProperties = copy.deepcopy(self._listProperties[:])
        for prop in item.listProperties:
            try:
                prop.value = getattr(item, prop.name)
                if prop.typeName == 'BlobProperty':
                    prop.meta = _getBlobProperties(item, prop.name)
            except datastore_errors.Error, exc:
                # Error is raised if referenced property is deleted
                # Catch the exception and set value to none
                logging.warning('Error catched in ModelAdmin._attachListFields: %s' % exc)
                prop.value = None
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
            [r'^/([^/]+)/get_blob_contents/([^/]+)/([^/]+)/$', self.get_blob_contents],
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
        for field in editProperties:
            if field.typeName == 'ReferenceProperty':
                field.referencedItems = field.prop.reference_class.all()
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
        for field in modelAdmin._editProperties:
            logging.info("Property: %s" % field.name)
            # detect preferred data type of property
            data_type = field.prop.data_type
            logging.info("Property type: %s" % data_type)
            # Since basestring can not be directly instantiated use unicode everywhere
            # Not yet decided what to do if non unicode data received.
            if data_type is basestring:
                data_type = unicode
            if field.typeName == 'BlobProperty':
                data_type = str
                uploadedFile = self.request.POST.get(field.name)
                metaData = {
                    'Content-Type': uploadedFile.type,
                    'File-Name': uploadedFile.filename
                }
                logging.info("Caching meta data for BlobProperty: %r" % metaData)
                attributes[field.name + BLOB_FIELD_META_SUFFIX] = pickle.dumps(metaData)
            if issubclass(data_type, db.Model):
                attributes[field.name] = data_type.get(self.request.get(field.name))
            else:
                attributes[field.name] = data_type(self.request.get(field.name))
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
            if editProperties[i].typeName == 'BlobProperty':
                logging.info("%s :: Binary content" % editProperties[i].name)
                editProperties[i].meta = _getBlobProperties(item, editProperties[i].name)
            else:
                logging.info("%s :: %s" % (editProperties[i].name, editProperties[i].value))
            if editProperties[i].typeName == 'ReferenceProperty':
                editProperties[i].referencedItems = editProperties[i].prop.reference_class.all()
        for i in range(len(readonlyProperties)):
            itemValue = getattr(item, readonlyProperties[i].name)
            readonlyProperties[i].value = itemValue
            if readonlyProperties[i].typeName == 'BlobProperty':
                logging.info("%s :: Binary content" % readonlyProperties[i].name)
                readonlyProperties[i].meta = _getBlobProperties(item, readonlyProperties[i].name)
            else:
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
        for field in modelAdmin._editProperties:
            # detect preferred data type of field
            data_type = field.prop.data_type
            # Since basestring can not be directly instantiated use unicode everywhere
            # Not yet decided what to do if non unicode data received.
            if data_type is basestring:
                data_type = unicode
            if field.typeName == 'BlobProperty':
                data_type = str
                uploadedFile = self.request.POST.get(field.name)
                metaFieldName = field.name + BLOB_FIELD_META_SUFFIX
                if getattr(item, metaFieldName, None):
                    metaData = {
                        'Content_Type': uploadedFile.type,
                        'File_Name': uploadedFile.filename
                    }
                    logging.info("Caching meta data for BlobProperty: %r" % metaData)
                    setattr(item, metaFieldName, pickle.dumps(metaData))
            if issubclass(data_type, db.Model):
                value = data_type.get(self.request.get(field.name))
            else:
                value = data_type(self.request.get(field.name))
            setattr(item, field.name, value)
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
    
    @authorized.role("admin")    
    def get_blob_contents(self, modelName, fieldName, key):
        """Returns blob field contents to user for downloading.
        """
        modelAdmin = getModelAdmin(modelName)
        item = self._safeGetItem(modelAdmin.model, key)
        data = getattr(item, fieldName, None)
        if data is None:
            raise Http404()
        else:
            props = _getBlobProperties(item, fieldName)
            if props:
                self.response.headers['Content-Type'] = props['Content_Type']
                self.response.headers['Content-Disposition'] = 'inline; filename=%s' % props['File_Name']
                logging.info("Setting content type to %s" % props['Content_Type'])
            else:
                self.response.headers['Content-Type'] = 'application/octet-stream'
            self.response.out.write(data)


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

def _getBlobProperties(item, fieldName):
    props = getattr(item, fieldName + BLOB_FIELD_META_SUFFIX, None)
    if props:
        return pickle.loads(props)
    else:
        return None
