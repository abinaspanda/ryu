# Copyright (C) 2012 Nippon Telegraph and Telephone Corporation.
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

# REST API using OFPMessages in JSON
#
# usage:
#
# curl -X POST -d
# '{
#      "OFPFlowMod":{
#          "table_id":0,
#          "instructions":[
#              {
#                  "OFPInstructionActions":{
#                      "actions":[
#                          {
#                              "OFPActionOutput":{
#                                  "port":2
#                              }
#                          }
#                      ],
#                      "type":4
#                  }
#              }
#          ]
#      }
#  }' http://localhost:8080/rest/1
#
#
# curl -X POST -d
# '{
#      "OFPFlowStatsRequest":{}
#  }' http://localhost:8080/rest/1


import json
import logging

from webob import Response

from ryu.app.wsgi import ControllerBase
from ryu.app.wsgi import WSGIApplication
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller import dpset
from ryu.controller.handler import MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.lib import hub
from ryu.ofproto import ofproto_v1_0
from ryu.ofproto import ofproto_v1_2
from ryu.ofproto import ofproto_v1_3
from ryu.ofproto import ofproto_v1_4


LOG = logging.getLogger(__name__)


class StatsController(ControllerBase):
    def __init__(self, req, link, data, **config):
        super(StatsController, self).__init__(req, link, data, **config)
        self.dpset = data['dpset']
        self.waiters = data['waiters']

    def send_msg(self, req, dpid):
        def _crlf(msg):
            return msg + '\n'

        dp = self.dpset.get(int(dpid, 16))
        if dp is None:
            msg = 'invalid datapath id: {0}'.format(dpid)
            LOG.error(msg)
            return Response(status=404, body=_crlf(msg))

        try:
            dic = eval(req.body)
        except SyntaxError:
            msg = "invalid syntax: '{0}'".format(req.body)
            LOG.error(msg)
            return Response(status=400, body=_crlf(msg))

        key, value = dic.popitem()
        cls = getattr(dp.ofproto_parser, key)
        msg = cls.from_jsondict(value, datapath=dp)

        msgs = []
        dp.set_xid(msg)
        waiters_per_dp = self.waiters.setdefault(dp.id, {})
        lock = hub.Event()
        waiters_per_dp[msg.xid] = (lock, msgs)
        dp.send_msg(msg)

        try:
            lock.wait(timeout=1.0)
        except hub.Timeout:
            del waiters_per_dp[msg.xid]
            msg = 'timeout.'
            LOG.error(msg)
            return Response(status=500, body=_crlf(msg))

        if len(msgs):
            body = _crlf(json.dumps(msgs[0].to_jsondict(), indent=2))
            return Response(content_type='application/json', body=body)
        else:
            msg = 'OK.'
            LOG.info(msg)
            return Response(status=200, body=_crlf(msg))


class RestStatsApi(app_manager.RyuApp):

    OFP_VERSIONS = [ofproto_v1_0.OFP_VERSION,
                    ofproto_v1_2.OFP_VERSION,
                    ofproto_v1_3.OFP_VERSION,
                    ofproto_v1_4.OFP_VERSION]
    _CONTEXTS = {
        'dpset': dpset.DPSet,
        'wsgi': WSGIApplication
    }

    def __init__(self, *args, **kwargs):
        super(RestStatsApi, self).__init__(*args, **kwargs)
        self.dpset = kwargs['dpset']
        wsgi = kwargs['wsgi']
        self.waiters = {}
        self.data = {}
        self.data['dpset'] = self.dpset
        self.data['waiters'] = self.waiters
        mapper = wsgi.mapper

        wsgi.registory['StatsController'] = self.data
        mapper.connect('rest', '/rest/{dpid}',
                       controller=StatsController, action='send_msg',
                       conditions=dict(method=['POST']))

    @set_ev_cls([ofp_event.EventOFPGetConfigReply,
                 ofp_event.EventOFPDescStatsReply,
                 ofp_event.EventOFPFlowStatsReply,
                 ofp_event.EventOFPAggregateStatsReply,
                 ofp_event.EventOFPTableStatsReply,
                 ofp_event.EventOFPPortStatsReply,
                 ofp_event.EventOFPQueueStatsReply,
                 ofp_event.EventOFPGroupStatsReply,
                 ofp_event.EventOFPGroupDescStatsReply,
                 ofp_event.EventOFPGroupFeaturesStatsReply,
                 ofp_event.EventOFPMeterStatsReply,
                 ofp_event.EventOFPMeterConfigStatsReply,
                 ofp_event.EventOFPMeterFeaturesStatsReply,
                 ofp_event.EventOFPTableFeaturesStatsReply,
                 ofp_event.EventOFPPortDescStatsReply,
                 ofp_event.EventOFPExperimenterStatsReply,
                 ofp_event.EventOFPQueueGetConfigReply,
                 ofp_event.EventOFPBarrierReply,
                 ofp_event.EventOFPRoleReply,
                 ofp_event.EventOFPGetAsyncReply], MAIN_DISPATCHER)
    def reply_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath

        if dp.id not in self.waiters:
            return
        if msg.xid not in self.waiters[dp.id]:
            return
        lock, msgs = self.waiters[dp.id][msg.xid]
        msgs.append(msg)

        del self.waiters[dp.id][msg.xid]
        lock.set()

    @set_ev_cls([ofp_event.EventOFPPacketIn,
                 ofp_event.EventOFPFlowRemoved,
                 ofp_event.EventOFPPortStatus,
                 ofp_event.EventOFPErrorMsg], MAIN_DISPATCHER)
    def async_message_handler(self, ev):
        self.logger.info(json.dumps(ev.msg.to_jsondict(), indent=2))
