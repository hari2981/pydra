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
from threading import Thread, Lock
from twisted.internet import reactor, threads

from pydra_server.cluster.tasks.tasks import Task
from pydra_server.cluster.tasks import TaskNotFoundException, STATUS_CANCELLED, STATUS_CANCELLED,\
    STATUS_FAILED,STATUS_STOPPED,STATUS_RUNNING,STATUS_PAUSED,STATUS_COMPLETE

import logging
logger = logging.getLogger('root')

class ParallelTask(Task):
    """
    ParallelTask - is a task that can be broken into discrete work units
    """
    _lock = None                # general lock
    _available_workers = 1      # number of workers available to this task
    _data = None                # list of data for this task
    _data_in_progress = {}      # workunits of data
    _workunit_count = 0         # count of workunits handed out.  This is used to identify transactions
    subtask = None              # subtask that is parallelized
    subtask_key = None          # cached key from subtask

    def __init__(self, msg=None):
        Task.__init__(self, msg)
        self._lock = Lock()

    def __setattr__(self, key, value):
        Task.__setattr__(self, key, value)
        if key == 'subtask':
            value.parent = self



    def work(self, args, callback, callback_args={}):
        """
        overridden to prevent early task cleanup.  ParallelTasl._work() returns immediately even though 
        work is likely running in the background.  There appears to be no effective way to block without 
        interupting twisted.Reactor.  The cleanup that normally happens in work() has been moved to
        task_complete() which will be called when there is no more work remaining.
        """
        self.__callback = callback
        self._callback_args=callback_args

        self._status = STATUS_RUNNING
        results = self._work(**args)

        return results


    def progress(self):
        """
        progress - returns the progress as a number 0-100.

        A parallel task's progress is a derivitive of its workunits:
           COMPLETE_WORKUNITS / TOTAL_WORKUNITS
        """
        return -1


    def _stop(self):
        """
        Overridden to call stop on all children
        """
        Task._stop(self)
        self.subtask._stop()


    def task_complete(self, results):
        """
        Should be called when all workunits have completed
        """
        self._status = STATUS_COMPLETE

        #make a callback, if any
        if self.__callback:
            self.__callback(results)


    def _work(self, **kwargs):
        """
        Work function overridden to delegate workunits to other Workers.
        """

        # save data, if any
        if kwargs and kwargs.has_key('data'):
            self._data = kwargs['data']
            logger.debug('Paralleltask - data was passed in!!!!')

        self.subtask_key = self.subtask._generate_key()

        logger.debug('Paralleltask - getting count of available workers')
        self._available_workers = self.get_worker().available_workers
        logger.debug('Paralleltask - starting, workers available: %i' % self._available_workers)


        # if theres more than one worker assign values
        # this check is required for cases where this is run
        # on a single core machine.  in that case this worker
        # is the only worker that exists
        if self._available_workers > 1:
            #assign initial set of work to other workers
            for i in range(1, self._available_workers):
                logger.debug('Paralleltask - trying to assign worker %i' % i)
                self._assign_work()

        #start a work_unit locally
        #reactor.callLater(1, self._assign_work_local)
        self._assign_work_local()

        logger.debug('Paralleltask - initial work assigned')
        # loop until all the data is processed
        reactor.callLater(5, self.more_work)


    def more_work(self):
        # check to see if there is either work in progress or work left to process
            with self._lock:
                if self.STOP_FLAG:
                    self.task_complete(None)

                else:
                    #check for more work
                    if len(self._data_in_progress) or len(self._data):
                        logger.debug('Paralleltask - still has more work: %s :  %s' % (self._data, self._data_in_progress))
                        reactor.callLater(5, self.more_work)

                    #all work is done, call the task specific function to combine the results 
                    else:
                        results = self.work_complete()
                        self.task_complete(results)


    def get_subtask(self, task_path):
        if len(task_path) == 1:
            if task_path[0] == self.__class__.__name__:
                return self
            else:
                raise TaskNotFoundException("Task not found: %s" % task_path)

        #pop this class off the list
        task_path.pop(0)

        #recurse down into the child
        return self.subtask.get_subtask(task_path)


    def _assign_work(self):
        """
        assign a unit of work to a Worker by requesting a worker from the compute cluster
        """
        data, index = self.get_work_unit()
        if not data == None:
            logger.debug('Paralleltask - assigning remote work')
            self.parent.request_worker(self.subtask.get_key(), {'data':data}, index)


    def _assign_work_local(self):
        """
        assign a unit of work to this Worker
        """
        logger.debug('Paralleltask - assigning work locally')
        data, index = self.get_work_unit()
        if not data == None:
            logger.debug('Paralleltask - starting work locally')
            self.subtask.start({'data':data}, callback=self._local_work_unit_complete, callback_args={'index':index})
        else:
            logger.debug('Paralleltask - no worker retrieved, idling')


    def get_work_unit(self):
        """
        Get the next work unit, by default a ParallelTask expects a list of values/tuples.
        When a arg is retrieved its removed from the list and placed in the in progress list.
        The arg is saved so that if the node fails the args can be re-run on another node

        This method *MUST* lock while it is altering the lists of data
        """
        logger.debug('Paralleltask - getting a workunit')
        data = None
        with self._lock:

            #grab from the beginning of the list
            data = self._data.pop(0)
            self._workunit_count += 1

            #remove from _data and add to in_progress
            self._data_in_progress[self._workunit_count] = data
        logger.debug('Paralleltask - got a workunit: %s %s' % (data, self._workunit_count))

        return data, self._workunit_count;


    def _work_unit_complete(self, results, index):
        """
        A work unit completed.  Handle the common management tasks to remove the data
        from in_progress.  Also call task specific work_unit_complete(...)

        This method *MUST* lock while it is altering the lists of data
        """
        logger.debug('Paralleltask - REMOTE Work unit completed')
        with self._lock:
            # run the task specific post process
            self.work_unit_complete(self._data_in_progress[index], results)

            # remove the workunit from _in_progress
            del self._data_in_progress[index]

        # start another work unit.  its possible there is only 1 unit left and multiple
        # workers completing at the same time reaching this call.  _assign_work() 
        # will handle the locking.  It will cause some threads to fail to get work but
        # that is expected.
        if len(self._data):
            self._assign_work()


    def _local_work_unit_complete(self, results, index):
        """
        A work unit completed.  Handle the common management tasks to remove the data
        from in_progress.  Also call task specific work_unit_complete(...)

        This method *MUST* lock while it is altering the lists of data
        """
        logger.debug('Paralleltask - LOCAL work unit completed')
        with self._lock:
            # run the task specific post process
            self.work_unit_complete(self._data_in_progress[index], results)

            # remove the workunit from _in_progress
            del self._data_in_progress[index]

        # start another work unit
        if len(self._data):
            self._assign_work_local()


    def _worker_failed(self, index):
        """
        A worker failed while working.  re-add the data to the list
        """
        logger.warning('Paralleltask - Worker failure during workunit')
        with self._lock:

            #remove data from in progress
            data = self._data_in_progress[index]
            del self._data_in_progress[index]

            #add data to the end of the list
            self._data.append(data)