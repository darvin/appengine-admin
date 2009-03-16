"""
Some utilities
"""
import pickle
import logging
import math

from . import admin_settings

def getBlobProperties(item, fieldName):
    props = getattr(item, fieldName + admin_settings.BLOB_FIELD_META_SUFFIX, None)
    if props:
        return pickle.loads(props)
    else:
        return None

class Http404(Exception):
    code = 404

class Http500(Exception):
    code = 500

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
