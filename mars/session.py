#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright 1999-2018 Alibaba Group Holding Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import numpy as np

try:
    from .resource import cpu_count
except ImportError:  # pragma: no cover
    from multiprocessing import cpu_count


class LocalSession(object):
    def __init__(self):
        from .executor import Executor

        self._executor = Executor()
        self._endpoint = None
        self._executed_tensors = set()

    @property
    def endpoint(self):
        return self._endpoint

    @endpoint.setter
    def endpoint(self, endpoint):
        if endpoint is not None:
            raise ValueError('Local session cannot set endpoint')
        self._endpoint = endpoint

    def run(self, *tensors, **kw):
        if self._executor is None:
            raise RuntimeError('Session has closed')
        if 'n_parallel' not in kw:
            kw['n_parallel'] = cpu_count()
        res = self._executor.execute_tensors(tensors, **kw)
        self._executed_tensors.update(t.key for t in tensors)
        return res

    def _update_tensor_shape(self, tensor):
        new_nsplits = self._executor.get_tensor_nsplits(tensor)
        tensor._update_shape(tuple(sum(nsplit) for nsplit in new_nsplits))
        tensor.nsplits = new_nsplits

    def fetch(self, *tensors, **kw):
        for t in tensors:
            if t.key not in self._executed_tensors:
                raise ValueError('Cannot fetch the unexecuted tensor')
        if self._executor is None:
            raise RuntimeError('Session has closed')
        if 'n_parallel' not in kw:
            kw['n_parallel'] = cpu_count()
        return self._executor.fetch_tensors(tensors, **kw)

    def decref(self, *keys):
        self._executor.decref(*keys)
        self._executed_tensors = self._executed_tensors.difference([k[0] for k in keys])

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self._executor = None


class Session(object):
    _default_session = None

    def __init__(self, endpoint=None):
        if endpoint is not None:
            if 'http' in endpoint:
                # connect to web
                from .web.session import Session as WebSession

                self._sess = WebSession(endpoint)
            else:
                # connect to local cluster
                from .deploy.local.session import LocalClusterSession

                self._sess = LocalClusterSession(endpoint)
        else:
            self._sess = LocalSession()

    def run(self, *tensors, **kw):
        from . import tensor as mt

        fetch = kw.get('fetch', True)
        ret_list = False
        if len(tensors) == 1 and isinstance(tensors[0], (tuple, list)):
            ret_list = True
            tensors = tensors[0]
        elif len(tensors) > 1:
            ret_list = True

        tensors = tuple(mt.tensor(t) for t in tensors)
        result = self._sess.run(*tensors, **kw)

        for t in tensors:
            t._execute_session = self

        for t in tensors:
            if np.nan in t.shape:
                self._sess._update_tensor_shape(t)

        if fetch:
            ret = []
            for r, t in zip(result, tensors):
                if t.isscalar() and hasattr(r, 'item'):
                    ret.append(np.asscalar(r))
                else:
                    ret.append(r)
            if ret_list:
                return ret
            return ret[0]

    def fetch(self, *tensors, **kw):
        ret_list = False
        if len(tensors) == 1 and isinstance(tensors[0], (tuple, list)):
            ret_list = True
            tensors = tensors[0]
        elif len(tensors) > 1:
            ret_list = True

        result = self._sess.fetch(*tensors, **kw)

        ret = []
        for r, t in zip(result, tensors):
            if t.isscalar() and hasattr(r, 'item'):
                ret.append(np.asscalar(r))
            else:
                ret.append(r)
        if ret_list:
            return ret
        return ret[0]

    @property
    def endpoint(self):
        return self._sess.endpoint

    @endpoint.setter
    def endpoint(self, endpoint):
        self._sess.endpoint = endpoint

    def decref(self, *keys):
        if hasattr(self._sess, 'decref'):
            self._sess.decref(*keys)

    def __getattr__(self, attr):
        obj = self._sess.__getattribute__(attr)
        return obj

    def __enter__(self):
        self._sess.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._sess.__exit__(exc_type, exc_val, exc_tb)

    def close(self):
        self.__exit__(None, None, None)

    def as_default(self):
        Session._default_session = self
        return self

    @classmethod
    def default_or_local(cls):
        if cls._default_session is not None:
            return cls._default_session

        cls._default_session = Session()
        return cls._default_session


def new_session(scheduler=None):
    return Session(scheduler)
