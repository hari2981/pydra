"""
    Copyright 2009 Oregon State University

    This file is part of Pydra.

    Pydra is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    Pydra is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with Pydra.  If not, see <http://www.gnu.org/licenses/>.
"""
from __future__ import with_statement
from threading import Lock

import settings

from twisted.application import internet
from twisted.cred import checkers, portal
from twisted.spread import pb

from pydra_server.models import pydraSettings
from pydra_server.cluster.auth.node_realm import NodeRealm
from pydra_server.cluster.auth.rsa_auth import load_crypto
from pydra_server.cluster.auth.worker_avatar import WorkerAvatar
from pydra_server.cluster.constants import *
from pydra_server.cluster.module import Module


# init logging
import logging
logger = logging.getLogger('root')


class WorkerConnectionManager(Module):

    _signals = [
        'WORKER_CONNECTED',
        'WORKER_DISCONNECTED',
    ]

    _shared = [
        'workers',
        'info'
    ]

    def __init__(self, manager):
        self._services = [self.get_worker_service]
        self._listeners = {'NODE_INITIALIZED':self.enable_workers}
        Module.__init__(self, manager)

        #locks
        self._lock = Lock() #general lock, use when multiple shared resources are touched

        #load rsa crypto
        self.pub_key, self.priv_key = load_crypto('./node.key')

        #cluster management
        self.workers = {}

        # setup worker security - using this checker just because we need
        # _something_ that returns an avatarID.  Its extremely vulnerable
        # but thats ok because the real authentication takes place after
        # the worker has connected
        self.worker_checker = checkers.InMemoryUsernamePasswordDatabaseDontUse()



    def enable_workers(self, node_key):
        """
        Enables workers to login.  cannot happen until the Node has received
        its node_key from the master.
        """
        for i in range(self.info['cores']):
            logger.debug('enabling worker: %s:%i' % (node_key, i) )
            self.worker_checker.addUser('%s:%s' % (node_key, i) , '1234')


    def get_worker_service(self, master):
        """
        constructs a twisted service for Workers to connect to 
        """
        logger.info('WorkerConnectionManager - starting server on port %s' % pydraSettings.port)

        # setup cluster connections
        realm = NodeRealm()
        realm.server = self

        p = portal.Portal(realm, [self.worker_checker])
 
        return internet.TCPServer(pydraSettings.port, pb.PBServerFactory(p))


    def worker_authenticated(self, worker_avatar):
        """
        Callback when a worker has been successfully authenticated
        """
        with self._lock:
            self.workers[worker_avatar.name] = worker_avatar

        self.emit('WORKER_CONNECTED', worker_avatar)


    def worker_disconnected(self, worker):
        """
        Callback from worker_avatar when it is disconnected
        """
        with self._lock:
            del self.workers[worker]

        self.emit('WORKER_DISCONNECTED', worker)
        

    

