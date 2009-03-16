"""Admin views"""

import os.path
import logging
import re
import copy

from google.appengine.ext import webapp
from google.appengine.api import datastore_errors
from google.appengine.ext.webapp import template

from . import authorized
from . import utils
from . import admin_settings
from . import model_register
from .model_register import getModelAdmin
from .utils import Http404, Http500

ADMIN_TEMPLATE_DIR = admin_settings.ADMIN_TEMPLATE_DIR
ADMIN_ITEMS_PER_PAGE = admin_settings.ADMIN_ITEMS_PER_PAGE

class BaseRequestHandler(webapp.RequestHandler):
    def handle_exception(self, exception, debug_mode):
        logging.warning("Exception catched: %r" % exception)
        if isinstance(exception, Http404) or isinstance(exception, Http500):
            self.error(exception.code)
            path = os.path.join(ADMIN_TEMPLATE_DIR, str(exception.code) + ".html")
            self.response.out.write(template.render(path, {'errorpage': True}))
        else:
            super(BaseRequestHandler, self).handle_exception(exception, debug_mode)


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
        self.models = model_register._modelRegister.keys()
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

    @staticmethod
    def _readonlyPropsWithValues(item, modelAdmin):
        readonlyProperties = copy.deepcopy(modelAdmin._readonlyProperties)
        for i in range(len(readonlyProperties)):
            itemValue = getattr(item, readonlyProperties[i].name)
            readonlyProperties[i].value = itemValue
            if readonlyProperties[i].typeName == 'BlobProperty':
                logging.info("%s :: Binary content" % readonlyProperties[i].name)
                readonlyProperties[i].meta = utils.getBlobProperties(item, readonlyProperties[i].name)
                if readonlyProperties[i].value:
                    readonlyProperties[i].value = True # release the memory
            else:
                logging.info("%s :: %s" % (readonlyProperties[i].name, readonlyProperties[i].value))
        return readonlyProperties


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
        page = utils.Page(
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

        templateValues = {
            'models': self.models,
            'urlPrefix': self.urlPrefix,
            'item' : None,
            'moduleTitle': modelAdmin.modelName,
            'editForm': modelAdmin.AdminForm(urlPrefix = self.urlPrefix),
            'readonlyProperties': modelAdmin._readonlyProperties,
        }
        path = os.path.join(ADMIN_TEMPLATE_DIR, 'model_item_edit.html')
        self.response.out.write(template.render(path, templateValues))

    @authorized.role("admin")
    def new_post(self, modelName):
        """Create new record of particular model
        """
        modelAdmin = getModelAdmin(modelName)
        form = modelAdmin.AdminForm(urlPrefix = self.urlPrefix, data = self.request.POST)
        if form.is_valid():
        # Save the data, and redirect to the edit page
            item = form.save()
            self.redirect("%s/%s/edit/%s/" % (self.urlPrefix, modelAdmin.modelName, item.key()))
        else:
            # Display errors with entered values
            templateValues = {
                'models': self.models,
                'urlPrefix': self.urlPrefix,
                'item' : None,
                'moduleTitle': modelAdmin.modelName,
                'editForm': form,
                'readonlyProperties': modelAdmin._readonlyProperties,
            }
            path = os.path.join(ADMIN_TEMPLATE_DIR, 'model_item_edit.html')
            self.response.out.write(template.render(path, templateValues))

    @authorized.role("admin")
    def edit_get(self, modelName, key = None):
        """Show for for editing existing record of particular model.
            Raises Http404 if record not found.
        """
        modelAdmin = getModelAdmin(modelName)
        item = self._safeGetItem(modelAdmin.model, key)
        templateValues = {
            'models': self.models,
            'urlPrefix': self.urlPrefix,
            'item' : item,
            'moduleTitle': modelAdmin.modelName,
            'editForm': modelAdmin.AdminForm(urlPrefix = self.urlPrefix, instance = item),
            'readonlyProperties': self._readonlyPropsWithValues(item, modelAdmin),
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
        form = modelAdmin.AdminForm(urlPrefix = self.urlPrefix, data = self.request.POST, instance = item)
        if form.is_valid():
        # Save the data, and redirect to the edit page
            item = form.save()
            self.redirect("%s/%s/edit/%s/" % (self.urlPrefix, modelAdmin.modelName, item.key()))
        else:
            templateValues = {
                'models': self.models,
                'urlPrefix': self.urlPrefix,
                'item' : item,
                'moduleTitle': modelAdmin.modelName,
                'editForm': form,
                'readonlyProperties': self._readonlyPropsWithValues(item, modelAdmin),
            }
            path = os.path.join(ADMIN_TEMPLATE_DIR, 'model_item_edit.html')
            self.response.out.write(template.render(path, templateValues))


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
            props = utils.getBlobProperties(item, fieldName)
            if props:
                self.response.headers['Content-Type'] = props['Content_Type']
                self.response.headers['Content-Disposition'] = 'inline; filename=%s' % props['File_Name']
                logging.info("Setting content type to %s" % props['Content_Type'])
            else:
                self.response.headers['Content-Type'] = 'application/octet-stream'
            self.response.out.write(data)
