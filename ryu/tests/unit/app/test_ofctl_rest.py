# Copyright (C) 2016 Nippon Telegraph and Telephone Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# vim: tabstop=4 shiftwidth=4 softtabstop=4

import functools
import unittest
import logging

from nose.tools import eq_
from webob.request import Request

from ryu.app.wsgi import WSGIApplication

from ryu.app import ofctl_rest
from ryu.controller import dpset
from routes import Mapper
from routes.util import URLGenerator

from ryu.ofproto import ofproto_protocol
from ryu.tests import test_lib

mapper = Mapper()

LOG = logging.getLogger('test_ofctl_rest')
DPID = 1
PORT = 1
QUEUE = 1


class DummyDatapath(ofproto_protocol.ProtocolDesc):

    def __init__(self, version):
        super(DummyDatapath, self).__init__(version)
        self.id = DPID
        self.request_msg = None
        self.reply_msg = None
        self.waiters = None
        port_info = self.ofproto_parser.OFPPort(
            port_no=1, hw_addr='ce:0f:31:8a:c8:d9', name='s1-eth1',
            config=1, state=1, curr=2112, advertised=0, supported=0,
            peer=0, curr_speed=10000000, max_speed=0)
        self.ports = {1: port_info}

    @staticmethod
    def set_xid(msg):
        msg.set_xid(0)
        return 0

    def send_msg(self, msg):
        msg.serialize()
        self.request_msg = msg

        if self.reply_msg:
            lock, msgs = self.waiters[self.id][msg.xid]
            msgs.append(self.reply_msg)
            del self.waiters[self.id][msg.xid]
            lock.set()

    def set_reply(self, msg, waiters):
        self.reply_msg = msg
        self.waiters = waiters


class Test_ofctl_rest(unittest.TestCase):

    def setUp(self):
        self.args = {
            'dpset': dpset.DPSet(),
            'wsgi': WSGIApplication()
        }
        self.ofctl_rest_app = ofctl_rest.RestStatsApi(**self.args)

    def tearDown(self):
        pass

    def _test(self, name, dp, method, path, args, body):
        print('processing %s ...' % name)

        # set static values
        r = Request.blank('')
        l = URLGenerator(Mapper(), r.environ)
        d = self.ofctl_rest_app.data
        d['dpset']._register(dp)

        # get a method of ofctl_rest
        dic = self.args['wsgi'].mapper.match(path, {'REQUEST_METHOD': method})
        controller = dic['controller'](r, l, d)
        action = dic['action']
        method = getattr(controller, action)

        # run the method
        req = Request.blank('')
        req.body = body
        reply = method(req, **args)

        # eq
        eq_(reply.status, '200 OK')


def _add_tests():
    _ofp_vers = {
        'of10': 0x01,
        'of12': 0x03,
        'of13': 0x04,
        'of14': 0x05,
        'of15': 0x06,
    }

    _test_cases = {
        'of13': [
                {
                    'method': 'GET',
                    'path': '/stats/switches',
                    'args': {}
                },
                {
                    'method': 'GET',
                    'path': '/stats/desc/{dpid}',
                    'args': {'dpid': DPID},
                },
                {
                    'method': 'GET',
                    'path': '/stats/flow/{dpid}',
                    'args': {'dpid': DPID},
                },
                {
                    'method': 'POST',
                    'path': '/stats/flow/{dpid}',
                    'args': {'dpid': DPID},
                    'body': '',
                },
                {
                    'method': 'GET',
                    'path': '/stats/aggregateflow/{dpid}',
                    'args': {'dpid': DPID},
                },
                {
                    'method': 'POST',
                    'path': '/stats/aggregateflow/{dpid}',
                    'args': {'dpid': DPID},
                    'body': '',
                },
                {
                    'method': 'GET',
                    'path': '/stats/table/{dpid}',
                    'args': {'dpid': DPID},
                },
                {
                    'method': 'GET',
                    'path': '/stats/tablefeatures/{dpid}',
                    'args': {'dpid': DPID},
                },
                {
                    'method': 'GET',
                    'path': '/stats/port/{dpid}',
                    'args': {'dpid': DPID},
                },
                {
                    'method': 'GET',
                    'path': '/stats/queue/{dpid}',
                    'args': {'dpid': DPID},
                },
                {
                    'method': 'GET',
                    'path': '/stats/queueconfig/{dpid}/{port}',
                    'args': {'dpid': DPID, 'port': PORT},
                },
                {
                    'method': 'GET',
                    'path': '/stats/meterfeatures/{dpid}',
                    'args': {'dpid': DPID},
                },
                {
                    'method': 'GET',
                    'path': '/stats/meterconfig/{dpid}',
                    'args': {'dpid': DPID},
                },
                {
                    'method': 'GET',
                    'path': '/stats/meter/{dpid}',
                    'args': {'dpid': DPID},
                },
                {
                    'method': 'GET',
                    'path': '/stats/groupfeatures/{dpid}',
                    'args': {'dpid': DPID},
                },
                {
                    'method': 'GET',
                    'path': '/stats/groupdesc/{dpid}',
                    'args': {'dpid': DPID},
                },
                {
                    'method': 'GET',
                    'path': '/stats/group/{dpid}',
                    'args': {'dpid': DPID},
                },
                {
                    'method': 'GET',
                    'path': '/stats/portdesc/{dpid}',
                    'args': {'dpid': DPID},
                },
                {
                    'method': 'POST',
                    'path': '/stats/flowentry/{cmd}',
                    'args': {'cmd': 'add'},
                    'body': '{"dpid": 1,"cookie": 1,"cookie_mask": 1,\
                        "table_id": 0,"idle_timeout": 30,\
                        "hard_timeout": 30,"priority": 11111,"flags": 1,\
                        "match":{"in_port":1},"actions":[{"type":"OUTPUT","port": 2}]}',
                },
                {
                    'method': 'POST',
                    'path': '/stats/flowentry/{cmd}',
                    'args': {'cmd': 'modify'},
                    'body': '{"dpid": 1,"cookie": 1,"cookie_mask": 1,\
                        "table_id": 0,"idle_timeout": 30,\
                        "hard_timeout": 30,"priority": 11111,"flags": 1,\
                        "match":{"in_port":1},"actions":[{"type":"OUTPUT","port": 2}]}',
                },
                {
                    'method': 'POST',
                    'path': '/stats/flowentry/{cmd}',
                    'args': {'cmd': 'modify_strict'},
                    'body': '{"dpid": 1,"cookie": 1,"cookie_mask": 1,\
                        "table_id": 0,"idle_timeout": 30,\
                        "hard_timeout": 30,"priority": 11111,"flags": 1,\
                        "match":{"in_port":1},"actions":[{"type":"OUTPUT","port": 2}]}',
                },
                {
                    'method': 'POST',
                    'path': '/stats/flowentry/{cmd}',
                    'args': {'cmd': 'delete'},
                    'body': '{"dpid": 1,"cookie": 1,"cookie_mask": 1,\
                        "table_id": 0,"idle_timeout": 30,\
                        "hard_timeout": 30,"priority": 11111,"flags": 1,\
                        "match":{"in_port":1},"actions":[{"type":"OUTPUT","port": 2}]}',
                },
                {
                    'method': 'POST',
                    'path': '/stats/flowentry/{cmd}',
                    'args': {'cmd': 'delete_strict'},
                    'body': '{"dpid": 1,"cookie": 1,"cookie_mask": 1,\
                        "table_id": 0,"idle_timeout": 30,\
                        "hard_timeout": 30,"priority": 11111,"flags": 1,\
                        "match":{"in_port":1},"actions":[{"type":"OUTPUT","port": 2}]}',
                },
                {
                    'method': 'DELETE',
                    'path': '/stats/flowentry/clear/{dpid}',
                    'args': {'dpid': DPID},
                },
                {
                    'method': 'POST',
                    'path': '/stats/meterentry/{cmd}',
                    'args': {'cmd': 'add'},
                    'body': '{"dpid": 1,"flags": "KBPS","meter_id": 1,\
                            "bands": [{"type": "DROP","rate": 1000}]\
                            }',
                },
                {
                    'method': 'POST',
                    'path': '/stats/meterentry/{cmd}',
                    'args': {'cmd': 'modify'},
                    'body': '{"dpid": 1,"flags": "KBPS","meter_id": 1,\
                            "bands": [{"type": "DROP","rate": 1000}]\
                            }',
                },
                {
                    'method': 'POST',
                    'path': '/stats/meterentry/{cmd}',
                    'args': {'cmd': 'delete'},
                    'body': '{"dpid": 1,"flags": "KBPS","meter_id": 1,\
                            "bands": [{"type": "DROP","rate": 1000}]\
                            }',
                },
                {
                    'method': 'POST',
                    'path': '/stats/groupentry/{cmd}',
                    'args': {'cmd': 'add'},
                    'body': '[{"actions": [{"type": "OUTPUT","port": 1}]}]',
                },
                {
                    'method': 'POST',
                    'path': '/stats/groupentry/{cmd}',
                    'args': {'cmd': 'modify'},
                    'body': '[{"actions": [{"type": "OUTPUT","port": 1}]}]',
                },
                {
                    'method': 'POST',
                    'path': '/stats/groupentry/{cmd}',
                    'args': {'cmd': 'delete'},
                    'body': '{"dpid": 1,"group_id": 1}',
                },
                {
                    'method': 'POST',
                    'path': '/stats/portdesc/{cmd}',
                    'args': {'cmd': 'modify'},
                    'body': '{"dpid": 1, "port_no": 1,"config": 1,"mask": 1}'

                },
                {
                    'method': 'POST',
                    'path': '/stats/experimenter/{dpid}',
                    'args': {'dpid': DPID},
                    'body': '{"dpid": 1,\
                        "experimenter": 1,"exp_type": 1,\
                        "data_type": "ascii","data": "data"\
                            }',
                },
        ]
    }

    for ofp_ver, tests in _test_cases.items():
        dp = DummyDatapath(_ofp_vers[ofp_ver])
        for test in tests:
            name = 'test_ofctl_rest_' + ofp_ver + '_' + test['path']
            print('adding %s ...' % name)
            f = functools.partial(
                Test_ofctl_rest._test, name=name, dp=dp,
                method=test['method'],
                path=test['path'],
                args=test['args'],
                body=test.get('body', '')
            )
            test_lib.add_method(Test_ofctl_rest, name, f)

_add_tests()

if __name__ == "__main__":
    unittest.main()
