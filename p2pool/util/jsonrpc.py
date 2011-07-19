from __future__ import division

import base64
import json

from twisted.internet import defer
from twisted.python import log
from twisted.web import client, error

import deferred_resource

class Error(Exception):
    def __init__(self, code, message, data=None):
        if not isinstance(code, int):
            raise TypeError('code must be an int')
        if not isinstance(message, unicode):
            raise TypeError('message must be a unicode')
        self._code, self._message, self._data = code, message, data
    def __str__(self):
        return '%i %s' % (self._code, self._message) + (' %r' % (self._data, ) if self._data is not None else '')
    def _to_obj(self):
        return {
            'code': self._code,
            'message': self._message,
            'data': self._data,
        }

class Proxy(object):
    def __init__(self, url, auth=None, timeout=5):
        self._url = url
        self._auth = auth
        self._timeout = timeout
    
    @defer.inlineCallbacks
    def callRemote(self, method, *params):
        id_ = 0
        
        headers = {
            'Content-Type': 'text/json',
        }
        if self._auth is not None:
            headers['Authorization'] = 'Basic ' + base64.b64encode(':'.join(self._auth))
        try:
            data = yield client.getPage(
                url=self._url,
                method='POST',
                headers=headers,
                postdata=json.dumps({
                    'jsonrpc': '2.0',
                    'method': method,
                    'params': params,
                    'id': id_,
                }),
                timeout=self._timeout,
            )
        except error.Error, e:
            try:
                resp = json.loads(e.response)
            except:
                raise e
        else:
            resp = json.loads(data)
        
        if resp['id'] != id_:
            raise ValueError('invalid id')
        if 'error' in resp and resp['error'] is not None:
            raise Error(**resp['error'])
        defer.returnValue(resp['result'])
    
    def __getattr__(self, attr):
        if attr.startswith('rpc_'):
            return lambda *params: self.callRemote(attr[len('rpc_'):], *params)
        raise AttributeError('%r object has no attribute %r' % (self.__class__.__name__, attr))

class Server(deferred_resource.DeferredResource):
    extra_headers = None
    
    @defer.inlineCallbacks
    def render_POST(self, request):
        # missing batching, 1.0 notifications
        data = request.content.read()
        
        if self.extra_headers is not None:
            for name, value in self.extra_headers.iteritems():
                request.setHeader(name, value)
        
        try:
            try:
                req = json.loads(data)
            except Exception:
                raise RemoteError(-32700, u'Parse error')
        except Error, e:
            # id unknown
            request.write(json.dumps({
                'jsonrpc': '2.0',
                'id': None,
                'result': None,
                'error': e._to_obj(),
            }))
        
        id_ = req.get('id', None)
        
        try:
            try:
                method = req['method']
                if not isinstance(method, unicode):
                    raise ValueError()
                params = req.get('params', [])
                if not isinstance(params, list):
                    raise ValueError()
            except Exception:
                raise Error(-32600, u'Invalid Request')
            
            method_name = 'rpc_' + method
            if not hasattr(self, method_name):
                raise Error(-32601, u'Method not found')
            method_meth = getattr(self, method_name)
            
            if hasattr(method_meth, "takes_headers"):
                params = [request.received_headers] + list(params)
            
            try:
                result = yield method_meth(*params)
            except Exception:
                print 'Squelched JSON method error:'
                log.err()
                raise Error(-32099, u'Unknown error')
            
            if id_ is None:
                return
            
            request.write(json.dumps({
                'jsonrpc': '2.0',
                'id': id_,
                'result': result,
                'error': None,
            }))
        except Error, e:
            request.write(json.dumps({
                'jsonrpc': '2.0',
                'id': id_,
                'result': None,
                'error': e._to_obj(),
            }))