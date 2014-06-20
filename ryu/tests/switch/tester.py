# Copyright (C) 2013 Nippon Telegraph and Telephone Corporation.
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


#★ クラス一覧
#class TestMessageBase(RyuException):
# →テストメッセージの基底クラス
#class TestFailure(TestMessageBase):
#class TestTimeout(TestMessageBase):
#class TestReceiveError(TestMessageBase):
#class TestError(TestMessageBase):
# →テスト失敗時のメッセージ
#class OfTester(app_manager.RyuApp):
# →アプリ本体。テストの実行機能を持つ。
#class OpenFlowSw(object):
# →OpenFlowスイッチを定義する。★object
#class TestPatterns(dict):
# →テストファイル全ての単位のインスタンス
#class TestFile(stringify.StringifyMixin):
# →テストファイル単体の単位のインスタンス
#class Test(stringify.StringifyMixin):
# →テスト１つ単位のインスタンス
#class DummyDatapath(object):
# →ダミーのスイッチインスタンス

#★ TODO＠現状の課題点
# Testerに関するやりとりで理解できていない点がある。
# Testerソースの中で理解できていない点がある。
# 　１。スループット
# 　２。ゆらぎ
# 　３。of14対応
# Tester追加パッチの内容で理解できていない点がある。
# Testerソースの中でどこが理解できていないか不明確。
# Testerのテストファイルがどこまで用意されているか不明
# GITの使い方に不明な点がある。
# OpenFlow仕様書の内容を把握しきれていない
#　→meter


#★ インポート
import binascii
import inspect
import json
import logging
import math
import netaddr
import os
import signal
import sys
import time
import traceback
from random import randint

from ryu import cfg

# import all packet libraries.
PKT_LIB_PATH = 'ryu.lib.packet'
for modname, moddef in sys.modules.iteritems():
    if not modname.startswith(PKT_LIB_PATH) or not moddef:
        continue
    for (clsname, clsdef, ) in inspect.getmembers(moddef):
        if not inspect.isclass(clsdef):
            continue
        exec 'from %s import %s' % (modname, clsname)

from ryu.base import app_manager
from ryu.controller import handler
from ryu.controller import ofp_event
from ryu.controller.handler import set_ev_cls
from ryu.exception import RyuException
from ryu.lib import dpid as dpid_lib
from ryu.lib import hub
from ryu.lib import stringify
from ryu.lib.packet import packet
from ryu.ofproto import ofproto_v1_3
from ryu.ofproto import ofproto_v1_3_parser


#(3)のケーブルは何のために使う？★
#Throughtput計測のためのマッチ条件とは？★
# →クッキー指定でフローを書き込んでいる

""" Required test network:

                      +-------------------+
           +----------|     target sw     | The switch to be tested
           |          +-------------------+
    +------------+      (1)   (2)   (3)
    | controller |       |     |     |
    +------------+      (1)   (2)   (3)
           |          +-------------------+
           +----------|     tester sw     | OpenFlow Switch
                      +-------------------+

      (X) : port number

    Tests send a packet from port 1 of the tester sw.
    If the packet matched with a flow entry of the target sw,
     the target sw resends the packet from port 2 (or the port which
     connected with the controller), according to the flow entry.
    Then the tester sw receives the packet and sends a PacketIn message.
    If the packet did not match, the target sw drops the packet.

"""


# CONFを取得
# この処理の意味の確認が必要★
CONF = cfg.CONF


# Default settings.
TESTER_SENDER_PORT = 1
TESTER_RECEIVE_PORT = 2
TARGET_SENDER_PORT = 2
TARGET_RECEIVE_PORT = 1

INTERVAL = 1  # sec
WAIT_TIMER = 3  # sec
CONTINUOUS_THREAD_INTVL = float(0.01)  # sec
CONTINUOUS_PROGRESS_SPAN = 3  # sec
THROUGHPUT_PRIORITY = ofproto_v1_3.OFP_DEFAULT_PRIORITY + 1
THROUGHPUT_COOKIE = THROUGHPUT_PRIORITY
THROUGHPUT_THRESHOLD = float(0.10)  # expected throughput plus/minus 10 %

# Default settings for 'ingress: packets'
DEFAULT_DURATION_TIME = 30
DEFAULT_PKTPS = 1000

# Test file format.
KEY_DESC = 'description'
KEY_PREREQ = 'prerequisite'
KEY_FLOW = 'OFPFlowMod'
KEY_METER = 'OFPMeterMod'
KEY_GROUP = 'OFPGroupMod'
KEY_TESTS = 'tests'
KEY_INGRESS = 'ingress'
KEY_EGRESS = 'egress'
KEY_PKT_IN = 'PACKET_IN'
KEY_TBL_MISS = 'table-miss'
KEY_PACKETS = 'packets'
KEY_DATA = 'data'
KEY_KBPS = 'kbps'
KEY_PKTPS = 'pktps'
KEY_DURATION_TIME = 'duration_time'
KEY_THROUGHPUT = 'throughput'
KEY_MATCH = 'OFPMatch'

# Test state.
STATE_INIT_FLOW = 0
STATE_FLOW_INSTALL = 1
STATE_FLOW_EXIST_CHK = 2
STATE_TARGET_PKT_COUNT = 3
STATE_TESTER_PKT_COUNT = 4
STATE_FLOW_MATCH_CHK = 5
STATE_NO_PKTIN_REASON = 6
STATE_GET_MATCH_COUNT = 7
STATE_SEND_BARRIER = 8
STATE_FLOW_UNMATCH_CHK = 9
STATE_INIT_METER = 10
STATE_METER_INSTALL = 11
STATE_METER_EXIST_CHK = 12
STATE_INIT_THROUGHPUT_FLOW = 13
STATE_THROUGHPUT_FLOW_INSTALL = 14
STATE_THROUGHPUT_FLOW_EXIST_CHK = 15
STATE_GET_THROUGHPUT = 16
STATE_THROUGHPUT_CHK = 17
STATE_INIT_GROUP = 18
STATE_GROUP_INSTALL = 19
STATE_GROUP_EXIST_CHK = 20

STATE_DISCONNECTED = 99

# Test result.
TEST_OK = 'OK'
TEST_ERROR = 'ERROR'
RYU_INTERNAL_ERROR = '- (Ryu internal error.)'
TEST_FILE_ERROR = '%(file)s : Test file format error (%(detail)s)'
NO_TEST_FILE = 'Test file (*.json) is not found.'
INVALID_PATH = '%(path)s : No such file or directory.'

# Test result details.
FAILURE = 0
ERROR = 1
TIMEOUT = 2
RCV_ERR = 3

MSG = {STATE_INIT_FLOW:
       {TIMEOUT: 'Failed to initialize flow tables: barrier request timeout.',
        RCV_ERR: 'Failed to initialize flow tables: %(err_msg)s'},
       STATE_INIT_THROUGHPUT_FLOW:
       {TIMEOUT: 'Failed to initialize flow tables of tester_sw: '
                 'barrier request timeout.',
        RCV_ERR: 'Failed to initialize flow tables of tester_sw: '
                 '%(err_msg)s'},
       STATE_FLOW_INSTALL:
       {TIMEOUT: 'Failed to add flows: barrier request timeout.',
        RCV_ERR: 'Failed to add flows: %(err_msg)s'},
       STATE_THROUGHPUT_FLOW_INSTALL:
       {TIMEOUT: 'Failed to add flows to tester_sw: barrier request timeout.',
        RCV_ERR: 'Failed to add flows to tester_sw: %(err_msg)s'},
       STATE_METER_INSTALL:
       {TIMEOUT: 'Failed to add meters: barrier request timeout.',
        RCV_ERR: 'Failed to add meters: %(err_msg)s'},
       STATE_GROUP_INSTALL:
       {TIMEOUT: 'Failed to add groups: barrier request timeout.',
        RCV_ERR: 'Failed to add groups: %(err_msg)s'},
       STATE_FLOW_EXIST_CHK:
       {FAILURE: 'Added incorrect flows: %(flows)s',
        TIMEOUT: 'Failed to add flows: flow stats request timeout.',
        RCV_ERR: 'Failed to add flows: %(err_msg)s'},
       STATE_METER_EXIST_CHK:
       {FAILURE: 'Added incorrect meters: %(meters)s',
        TIMEOUT: 'Failed to add meters: meter config stats request timeout.',
        RCV_ERR: 'Failed to add meters: %(err_msg)s'},
       STATE_GROUP_EXIST_CHK:
       {FAILURE: 'Added incorrect groups: %(groups)s',
        TIMEOUT: 'Failed to add groups: group desc stats request timeout.',
        RCV_ERR: 'Failed to add groups: %(err_msg)s'},
       STATE_TARGET_PKT_COUNT:
       {TIMEOUT: 'Failed to request port stats from target: request timeout.',
        RCV_ERR: 'Failed to request port stats from target: %(err_msg)s'},
       STATE_TESTER_PKT_COUNT:
       {TIMEOUT: 'Failed to request port stats from tester: request timeout.',
        RCV_ERR: 'Failed to request port stats from tester: %(err_msg)s'},
       STATE_FLOW_MATCH_CHK:
       {FAILURE: 'Received incorrect %(pkt_type)s: %(detail)s',
        TIMEOUT: '',  # for check no packet-in reason.
        RCV_ERR: 'Failed to send packet: %(err_msg)s'},
       STATE_NO_PKTIN_REASON:
       {FAILURE: 'Receiving timeout: %(detail)s'},
       STATE_GET_MATCH_COUNT:
       {TIMEOUT: 'Failed to request table stats: request timeout.',
        RCV_ERR: 'Failed to request table stats: %(err_msg)s'},
       STATE_SEND_BARRIER:
       {TIMEOUT: 'Faild to send packet: barrier request timeout.',
        RCV_ERR: 'Faild to send packet: %(err_msg)s'},
       STATE_FLOW_UNMATCH_CHK:
       {FAILURE: 'Table-miss error: increment in matched_count.',
        ERROR: 'Table-miss error: no change in lookup_count.',
        TIMEOUT: 'Failed to request table stats: request timeout.',
        RCV_ERR: 'Failed to request table stats: %(err_msg)s'},
       STATE_THROUGHPUT_FLOW_EXIST_CHK:
       {FAILURE: 'Added incorrect flows to tester_sw: %(flows)s',
        TIMEOUT: 'Failed to add flows to tester_sw: '
                 'flow stats request timeout.',
        RCV_ERR: 'Failed to add flows to tester_sw: %(err_msg)s'},
       STATE_GET_THROUGHPUT:
       {TIMEOUT: 'Failed to request flow stats: request timeout.',
        RCV_ERR: 'Failed to request flow stats: %(err_msg)s'},
       STATE_THROUGHPUT_CHK:
       {FAILURE: 'Received unexpected throughput: %(detail)s'},
       STATE_DISCONNECTED:
       {ERROR: 'Disconnected from switch'}}

ERR_MSG = 'OFPErrorMsg[type=0x%02x, code=0x%02x]'


# テストメッセージのベース
class TestMessageBase(RyuException):
    def __init__(self, state, message_type, **argv):
        msg = MSG[state][message_type] % argv
        super(TestMessageBase, self).__init__(msg=msg)

# テスト失敗時のメッセージ
class TestFailure(TestMessageBase):
    #**argv★
    def __init__(self, state, **argv):
        super(TestFailure, self).__init__(state, FAILURE, **argv)
        #                                           ↑メッセージタイプはFAILURE

class TestTimeout(TestMessageBase):
    def __init__(self, state):
        super(TestTimeout, self).__init__(state, TIMEOUT)


class TestReceiveError(TestMessageBase):
    def __init__(self, state, err_msg):
        argv = {'err_msg': ERR_MSG % (err_msg.type, err_msg.code)}
        super(TestReceiveError, self).__init__(state, RCV_ERR, **argv)


class TestError(TestMessageBase):
    def __init__(self, state, **argv):
        super(TestError, self).__init__(state, ERROR, **argv)


class OfTester(app_manager.RyuApp):
    """ OpenFlow Switch Tester. """

    # 1.OpenFlowバージョンを定義
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self):
        super(OfTester, self).__init__()

        # 2.ログを初期化
        self._set_logger()

        # 3.ターゲットSW及びテスターSWのDatapathIDを取得
        # →CONFから取得。ユーザ指定が無い場合はデフォルト値が設定される。
        #   CONF ...  .conf的な位置づけ
        self.target_dpid = self._convert_dpid(CONF['test-switch']['target'])
        self.tester_dpid = self._convert_dpid(CONF['test-switch']['tester'])

        【ログ出力】self.logger.info('target_dpid=%s',
                         dpid_lib.dpid_to_str(self.target_dpid))
        【ログ出力】self.logger.info('tester_dpid=%s',
                         dpid_lib.dpid_to_str(self.tester_dpid))
        # 4.テストスイッチのディレクトリを指定。（何を指定？★）
        test_dir = CONF['test-switch']['dir']
        【ログ出力】self.logger.info('Test files directory = %s', test_dir)

        # 5.ターゲットSW及びテスターSWのインスタンスを取得
        #   →アプリ起動時はSWと接続中の可能性があるため、ダミーのインスタンスを
        #　　　取得する。
        self.target_sw = OpenFlowSw(DummyDatapath(), self.logger)
        self.tester_sw = OpenFlowSw(DummyDatapath(), self.logger)
        # 6.状態を　STATE_INIT_FLOW　に設定。
        #　　→この状態はこのあと出てきた？★
        self.state = STATE_INIT_FLOW
        # 7.sw_waiter および waiter を設定
        # ★
        self.sw_waiter = None
        # ★
        self.waiter = None
        # 8.送信メッセージのxid保持用リストを初期化★
        self.send_msg_xids = []
        # 9.受信メッセージ自体の保持用リストを初期化★
        self.rcv_msgs = []
        # ★
        self.ingress_event = None
        # ★
        self.ingress_threads = []
        # ★
        self.thread_msg = None
        # サブスレッドでテスト実行開始（test_thread　にスレッド？？が入る？★）
        self.test_thread = hub.spawn(
            self._test_sequential_execute, test_dir)

    def _set_logger(self):
        self.logger.propagate = False
        s_hdlr = logging.StreamHandler()
        self.logger.addHandler(s_hdlr)
        if CONF.log_file:
            f_hdlr = logging.handlers.WatchedFileHandler(CONF.log_file)
            self.logger.addHandler(f_hdlr)

    def _convert_dpid(self, dpid_str):
        # DPIDの変換（16進数→INT）
        try:
            dpid = int(dpid_str, 16)
        except ValueError as err:
            self.logger.error('Invarid dpid parameter. %s', err)
            self._test_end()
        return dpid

    def close(self):
        # test_thread　に値が存在する→ kill
        if self.test_thread is not None:
            hub.kill(self.test_thread)
        # ingress_event が True　→　set　何の意味？★
        if self.ingress_event:
            self.ingress_event.set()
        # スレッドを配列に入れてjoinall
        hub.joinall([self.test_thread])
        self._test_end('--- Test terminated ---')

    #DISPATCHERがMAIN/DEAD時に実行される
    @set_ev_cls(ofp_event.EventOFPStateChange,
                [handler.MAIN_DISPATCHER, handler.DEAD_DISPATCHER])
    def dispacher_change(self, ev):
        #イベントのdatapath（not ID）に値が入っていなかったら
        # なぜdataoathに値が入っていない？？
        assert ev.datapath is not None
        #ステートがMAIN～だったら→登録処理
        if ev.state == handler.MAIN_DISPATCHER:
            self._register_sw(ev.datapath)
        #ステートがDEAD～だったら→登録解除処理
        elif ev.state == handler.DEAD_DISPATCHER:
            self._unregister_sw(ev.datapath)

    # SW登録処理
    def _register_sw(self, dp):
        # 1. データパスIDを設定
        if dp.id == self.target_dpid:
            self.target_sw.dp = dp
            msg = 'Join target SW.'
        elif dp.id == self.tester_dpid:
            self.tester_sw.dp = dp
            # フローを設定
            # →受信ポートにパケットが来たら、コントローラへ転送
            self.tester_sw.add_flow(
                in_port=TESTER_RECEIVE_PORT,
                out_port=dp.ofproto.OFPP_CONTROLLER)
            msg = 'Join tester SW.'
        else:
            msg = 'Connect unknown SW.'

        if dp.id:
            【ログ出力】self.logger.info('dpid=%s : %s',
                             dpid_lib.dpid_to_str(dp.id), msg)
        # 2. いずれかがダミーデータパスではない→どちらも接続OKだったら
        if not (isinstance(self.target_sw.dp, DummyDatapath) or
                isinstance(self.tester_sw.dp, DummyDatapath)):
            # sw_waiterはhub.Event()を入れて、SW接続を待つための変数
            # →有数＝スイッチ接続待ち
            # sw_waiterが有数だったら★
            if self.sw_waiter is not None:
                # sw_waiter.setを実行★
                self.sw_waiter.set()
                #eventlet.event.Event()の
                # self._ev.send()
                #eventletイベントは、1つのイベントをマルチに送信不可
                #のため、元になるイベントをもう一度作成する.
                #注意：_ev.reset（）は廃止されました。
                #もう一度Eventを取得
                #
                #つまり、test_waiterでwaitしている処理を、
                #本処理を実行することにより、処理再開させることができる

    # SW登録解除処理
    def _unregister_sw(self, dp):
        if dp.id == self.target_dpid:
            self.target_sw.dp = DummyDatapath()
            msg = 'Leave target SW.'
        elif dp.id == self.tester_dpid:
            self.tester_sw.dp = DummyDatapath()
            msg = 'Leave tester SW.'
        else:
            msg = 'Disconnect unknown SW.'
        if dp.id:
            【ログ出力】self.logger.info('dpid=%s : %s',
                             dpid_lib.dpid_to_str(dp.id), msg)

    # テスト実行１
    def _test_sequential_execute(self, test_dir):
        """ Execute OpenFlow Switch test. """
        # Parse test pattern from test files.
        tests = TestPatterns(test_dir, self.logger)
        if not tests:
            self.logger.warning(NO_TEST_FILE)
            self._test_end()

        test_report = {}
        【ログ出力】self.logger.info('--- Test start ---')
        test_keys = tests.keys()
        test_keys.sort()
        for file_name in test_keys:
            report = self._test_file_execute(tests[file_name])
            for result, descriptions in report.items():
                test_report.setdefault(result, [])
                test_report[result].extend(descriptions)
        self._test_end(msg='---  Test end  ---', report=test_report)

    # テスト実行２
    def _test_file_execute(self, testfile):
        report = {}
        for i, test in enumerate(testfile.tests):
            desc = testfile.description if i == 0 else None
            result = self._test_execute(test, desc)
            report.setdefault(result, [])
            report[result].append([testfile.description, test.description])
        return report

    # テスト実行３
    def _test_execute(self, test, description):

        # -----------------------------------------------------------------------
        # 1.SWとの接続待ち●
        # 　※SW接続が完了していなかった場合
        if isinstance(self.target_sw.dp, DummyDatapath) or \
                isinstance(self.tester_sw.dp, DummyDatapath):
            【ログ出力】self.logger.info('waiting for switches connection...')
            self.sw_waiter = hub.Event()
            #hub...本体はeventlet
            #hub.Event.set  ->
            #hub.Event.wait -> ジョブの完了を待つ
            self.sw_waiter.wait()
            #ジョブが完了したら、初期化（Noneを入れる）
            self.sw_waiter = None

        # -----------------------------------------------------------------------
        # 2.description出力（ファイルにおける１つめのテストのみ）
        if description:
            【ログ出力】self.logger.info('%s', description)
        # thread_msg を初期化（連続印加時に関連）
        self.thread_msg = None


        # Test execute.
        try:
            # Initialize.
            # -----------------------------------------------------------------------
            # 3.初期化
            # 　3-1.METER,GROUP,FLOW(ターゲット),FLOW(テスター＠クッキー指定)を削除
            self._test(STATE_INIT_METER)
            self._test(STATE_INIT_GROUP)
            self._test(STATE_INIT_FLOW, self.target_sw)
            self._test(STATE_INIT_THROUGHPUT_FLOW, self.tester_sw,
                       THROUGHPUT_COOKIE)

            # Install flows.
            # -----------------------------------------------------------------------
            # 4.prerequisiteに基づいてフローを設定
            #　　prerequisiteの具体的なイメージを確認★
            for flow in test.prerequisite:
                # flow には、バージョンに応じたクラスインスタンスが設定されている。
                if isinstance(flow, ofproto_v1_3_parser.OFPFlowMod):
                    # -------エントリ関連のインストール------------------
                    # 1. フローのインストール（ターゲット）
                    #● STATE_FLOW_INSTALL（設定）
                    self._test(STATE_FLOW_INSTALL, self.target_sw, flow)
                    #　　　　　　　▼
                    # 2. フローが正しく設定されたかチェック
                    #● STATE_FLOW_EXIST_CHK（設定確認）
                    self._test(STATE_FLOW_EXIST_CHK,
                               self.target_sw.send_flow_stats, flow)
                               #     ↑OpenFlowクラスのメソッド
                elif isinstance(flow, ofproto_v1_3_parser.OFPMeterMod):
                    #● STATE_METER_INSTALL（設定）
                    self._test(STATE_METER_INSTALL, self.target_sw, flow)
                    #　　　　　　　▼
                    #● STATE_METER_EXIST_CHK（設定確認）
                    self._test(STATE_METER_EXIST_CHK,
                               self.target_sw.send_meter_config_stats, flow)
                               #     ↑OpenFlowクラスのメソッド
                elif isinstance(flow, ofproto_v1_3_parser.OFPGroupMod):
                    #● STATE_GROUP_INSTALL（設定）
                    self._test(STATE_GROUP_INSTALL, self.target_sw, flow)
                    #　　　　　　　▼
                    #● STATE_GROUP_EXIST_CHK（設定確認）
                    self._test(STATE_GROUP_EXIST_CHK,
                               self.target_sw.send_group_desc_stats, flow)
                    # -------------------------------------------------
            # Do tests.
            for pkt in test.tests:
                # ※テストとして期待する結果は
                # 「スループット」「EGRESS（戻ってくる）」「パケットイン」
                # 「マッチせず」の４パターン
                # -----------------------------------------------------------------------
                ### 5.tests配列のpkt辞書にEGRESS or PKT_IN が含まれていた場合
                ### Get stats before sending packet(s).
                ###タイムアウト時の情報出力用
                ###● STATE_TARGET_PKT_COUNT（ポート統計）
                ###● STATE_TESTER_PKT_COUNT（ポート統計）
                if KEY_EGRESS in pkt or KEY_PKT_IN in pkt:
                    target_pkt_count = [self._test(STATE_TARGET_PKT_COUNT,
                                                   True)]
                    tester_pkt_count = [self._test(STATE_TESTER_PKT_COUNT,
                                                   False)]
                # 或いは
                # 5.tests配列のpkt辞書に KEY_THROUGHPUT が含まれていた場合
                #　　テスターのフロー統計を取得→試験後に比較
                elif KEY_THROUGHPUT in pkt:
                    # install flows for throughput analysis
                    for throughput in pkt[KEY_THROUGHPUT]:
                        flow = throughput[KEY_FLOW]
                        #● STATE_THROUGHPUT_FLOW_INSTALL（フロー設定）
                        self._test(STATE_THROUGHPUT_FLOW_INSTALL,#▼
                                   self.tester_sw, flow)         #▼
                        #● STATE_THROUGHPUT_FLOW_EXIST_CHK（設定確認）
                        self._test(STATE_THROUGHPUT_FLOW_EXIST_CHK,
                                   self.tester_sw.send_flow_stats, flow)
                    #● STATE_GET_THROUGHPUT（スループット取得）
                    # 　→スループット用のフローの
                    #     バイトカウント、パケットカウントを取得
                    start = self._test(STATE_GET_THROUGHPUT)
                # 或いは
                # 5.tests配列のpkt辞書に KEY_TBL_MISS が含まれていた場合
                elif KEY_TBL_MISS in pkt:
                    # ●●●STATE_GET_MATCH_COUNT（start）
                    # →検索の回数、マッチ回数を取得（試験後の成否チェックのために取得）
                    before_stats = self._test(STATE_GET_MATCH_COUNT)

                # -----------------------------------------------------------------------
                # Send packet(s).
                # パケット送信＠１つ
                if KEY_INGRESS in pkt:
                    self._one_time_packet_send(pkt)
                # パケット送信＠複数
                elif KEY_PACKETS in pkt:
                    self._continuous_packet_send(pkt)

                # -----------------------------------------------------------------------
                # Check a result.
                #
                # -----------------------------------
                if KEY_EGRESS in pkt or KEY_PKT_IN in pkt:
                    #● STATE_FLOW_MATCH_CHK
                    # テスト結果を確認（期待通りか？）
                    # ＠パケットインが期待通りの　DPID、パケットイン理由、データ内容　であることを期待
                    result = self._test(STATE_FLOW_MATCH_CHK, pkt)
                    # タイムアウト時処理
                    if result == TIMEOUT:
                        #● STATE_TARGET_PKT_COUNT（ポート統計）
                        #● STATE_TESTER_PKT_COUNT（ポート統計）
                        target_pkt_count.append(self._test(
                            STATE_TARGET_PKT_COUNT, True))
                        tester_pkt_count.append(self._test(
                            STATE_TESTER_PKT_COUNT, False))
                        test_type = (KEY_EGRESS if KEY_EGRESS in pkt
                                     else KEY_PKT_IN)
                        self._test(STATE_NO_PKTIN_REASON, test_type,
                                   target_pkt_count, tester_pkt_count)
                # -----------------------------------
                elif KEY_THROUGHPUT in pkt:
                    # スループット用の統計を取得（テスター側のフロー）
                    # ●●●STATE_GET_MATCH_COUNT（end）
                    # →検索の回数、マッチ回数を取得（試験後の比較のために取得）
                    end = self._test(STATE_GET_THROUGHPUT)
                    # テスト結果を確認＠_CHK
                    self._test(STATE_THROUGHPUT_CHK, pkt[KEY_THROUGHPUT],
                               start, end)
                # -----------------------------------
                elif KEY_TBL_MISS in pkt:
                    self._test(STATE_SEND_BARRIER)
                    hub.sleep(INTERVAL)
                    # テスト結果を確認＠マッチ回数ゼロであることを期待
                    self._test(STATE_FLOW_UNMATCH_CHK, before_stats, pkt)

            # -----------------------------------------------------------------------
            # テスト処理中に try catch ★ でエラー条件にひっかからなければココに到着
            # result に TEST_OK　（'OK'）　を設定
            result = [TEST_OK]
            # resultタイプ に TEST_OK　（'OK'）を設定
            result_type = TEST_OK
        except (TestFailure, TestError,
                TestTimeout, TestReceiveError) as err:
            result = [TEST_ERROR, str(err)]
            result_type = str(err).split(':', 1)[0]
        except Exception:
            result = [TEST_ERROR, RYU_INTERNAL_ERROR]
            result_type = RYU_INTERNAL_ERROR
        finally:
            # ingress_event をNoneに設定　意味は?★
            # →ingress_eventはパケット連続送信時に活用
            self.ingress_event = None
            for tid in self.ingress_threads:
                hub.kill(tid)
            self.ingress_threads = []

        # Output test result.
        【ログ出力】self.logger.info('    %-100s %s', test.description, result[0])
        if 1 < len(result):
            【ログ出力】self.logger.info('        %s', result[1])
            if (result[1] == RYU_INTERNAL_ERROR
                    or result == 'An unknown exception'):
                self.logger.error(traceback.format_exc())

        hub.sleep(0)
        return result_type

    # テスト終了H剃り
    def _test_end(self, msg=None, report=None):
        self.test_thread = None
        if msg:
            【ログ出力】self.logger.info(msg)
        if report:
            self._output_test_report(report)
        pid = os.getpid()
        os.kill(pid, signal.SIGTERM)

    # テストレポート処理
    def _output_test_report(self, report):
        【ログ出力】self.logger.info('%s--- Test report ---', os.linesep)
        error_count = 0
        for result_type in sorted(report.keys()):
            test_descriptions = report[result_type]
            if result_type == TEST_OK:
                continue
            error_count += len(test_descriptions)
            【ログ出力】self.logger.info('%s(%d)', result_type, len(test_descriptions))
            for file_desc, test_desc in test_descriptions:
                【ログ出力】self.logger.info('    %-40s %s', file_desc, test_desc)
        【ログ出力】self.logger.info('%s%s(%d) / %s(%d)', os.linesep,
                         TEST_OK, len(report.get(TEST_OK, [])),
                         TEST_ERROR, error_count)

    # テスト処理（主にオープンフローメッセージの送信を司る）
    def _test(self, state, *args):
        test = {STATE_INIT_FLOW: self._test_initialize_flow,
                STATE_INIT_THROUGHPUT_FLOW: self._test_initialize_flow,
                STATE_INIT_METER: self.target_sw.del_meters,
                STATE_INIT_GROUP: self.target_sw.del_groups,
                STATE_FLOW_INSTALL: self._test_msg_install,
                STATE_THROUGHPUT_FLOW_INSTALL: self._test_msg_install,
                STATE_METER_INSTALL: self._test_msg_install,
                STATE_GROUP_INSTALL: self._test_msg_install,
                STATE_FLOW_EXIST_CHK: self._test_exist_check,
                STATE_THROUGHPUT_FLOW_EXIST_CHK: self._test_exist_check,
                STATE_METER_EXIST_CHK: self._test_exist_check,
                STATE_GROUP_EXIST_CHK: self._test_exist_check,
                STATE_TARGET_PKT_COUNT: self._test_get_packet_count,
                STATE_TESTER_PKT_COUNT: self._test_get_packet_count,
                STATE_FLOW_MATCH_CHK: self._test_flow_matching_check,
                STATE_NO_PKTIN_REASON: self._test_no_pktin_reason_check,
                STATE_GET_MATCH_COUNT: self._test_get_match_count,
                STATE_SEND_BARRIER: self._test_send_barrier,
                STATE_FLOW_UNMATCH_CHK: self._test_flow_unmatching_check,
                STATE_GET_THROUGHPUT: self._test_get_throughput,
                STATE_THROUGHPUT_CHK: self._test_throughput_check}

        self.send_msg_xids = []
        self.rcv_msgs = []

        # stateを設定する。ポート統計取得中。。フロー設定中。。。など
        self.state = state
        return test[state](*args)

    #　※基本的にメッセージ送信系の流れは
    # 1　メッセージ送信　＆　send_msg_xidsにアペンド
    # 2　待ち
    # 3　rcv_msgsの結果確認
    #　テスト実行毎に　上記の配列は初期化される。
    #　
    #　send_msg_xids
    #　　→メッセージに対する応答返却時に、このxidを保持するメッセージのみ
    #  　　rcv_msgsにアペンドする機構となっている。

    # フロー消去
    def _test_initialize_flow(self, datapath, cookie=0):
        # １. メッセージ送信　＆　xidをアペンド
        xid = datapath.del_flows(cookie)
        self.send_msg_xids.append(xid)

        # １. メッセージ送信　＆　xidをアペンド
        xid = datapath.send_barrier_request()
        self.send_msg_xids.append(xid)

        # ２. 待ち
        self._wait() #-------------wait-------------

        # ３. 結果を確認（メッセージが１つでない場合、エラー）
        assert len(self.rcv_msgs) == 1
        msg = self.rcv_msgs[0]
        assert isinstance(msg, ofproto_v1_3_parser.OFPBarrierReply)

    # メッセージインストール
    def _test_msg_install(self, datapath, message):
        # １. メッセージ送信　＆　xidをアペンド
        xid = datapath.send_msg(message)
        self.send_msg_xids.append(xid)
        # １. メッセージ送信　＆　xidをアペンド
        xid = datapath.send_barrier_request()
        self.send_msg_xids.append(xid)

        # ２. 待ち
        self._wait() #-------------wait-------------

        # ３. 結果を確認
        assert len(self.rcv_msgs) == 1
        msg = self.rcv_msgs[0]
        assert isinstance(msg, ofproto_v1_3_parser.OFPBarrierReply)

    # きちんと設定できているか確認する（フロー、METER、GROUP）
    # 設定しようとしているモノ　＝　Statsで得られた結果
    # を比較する。（この中で compare flow (フローエントリ比較) も利用される）
    def _test_exist_check(self, method, message):
        #                        ↑swインスタンスの保持するメソッドを指定
        # message は、preリクワジットに記載されているもの

        # メソッド辞書を生成
        method_dict = {
            # {フロー統計取得メソッド(sw)：{ reply:リプライクラス, compare:比較メソッド } }
            OpenFlowSw.send_flow_stats.__name__: {
                'reply': ofproto_v1_3_parser.OFPFlowStatsReply,
                'compare': self._compare_flow
            },
            # {METER統計取得メソッド(sw)：{ reply:リプライクラス, compare:比較メソッド } }
            OpenFlowSw.send_meter_config_stats.__name__: {
                'reply': ofproto_v1_3_parser.OFPMeterConfigStatsReply,
                'compare': self._compare_meter
            },
            # {GROUP統計取得メソッド(sw)：{ reply:リプライクラス, compare:比較メソッド } }
            OpenFlowSw.send_group_desc_stats.__name__: {
                'reply': ofproto_v1_3_parser.OFPGroupDescStatsReply,
                'compare': self._compare_group
            }
        }
        # １. メッセージ送信　＆　xidをアペンド
        xid = method()
        self.send_msg_xids.append(xid)

        # ２. 待ち
        self._wait() #-------------wait-------------

        # ３. 結果を確認
        ng_stats = []
        for msg in self.rcv_msgs:
            #　　　　　　　　　　↓4. msg が、期待しているリプライかどうかチェック
            assert isinstance(msg, method_dict[method.__name__]['reply'])
            for stats in msg.body:
                #比較メソッドに　
                #                           ↓メソッド名
                result, stats = method_dict[method.__name__]['compare'](
                #        ↑正常時はNone 異常時はエラーメッセージが返される
                    stats, message)
                    #第一引数が、統計リプライそのもので、
                    #第二引数が、期待するメッセージ（jsonに記述されているもの）
                if result:
                    #resultを返却
                    return
                else:
                    ng_stats.append(stats)

        #エラー辞書に追加
        # → ng_stats の結果がすべてのキーに入る？？
        error_dict = {
            OpenFlowSw.send_flow_stats.__name__:
                {'flows': ', '.join(ng_stats)},
            OpenFlowSw.send_meter_config_stats.__name__:
                {'meters': ', '.join(ng_stats)},
            OpenFlowSw.send_group_desc_stats.__name__:
                {'groups': ', '.join(ng_stats)}
        }
        # raiseの意味は？★
        raise TestFailure(self.state, **error_dict[method.__name__])

    # ポートを取得して、結果を辞書型で返却
    def _test_get_packet_count(self, is_target):
        sw = self.target_sw if is_target else self.tester_sw
        # １. ポート統計取得を送信　＆　xidをアペンド
        xid = sw.send_port_stats()
        self.send_msg_xids.append(xid)
        # ２. 待ち
        self._wait() #-------------wait-------------
        result = {}
        # ３. result辞書に値を格納
        # key 値
        #  {1 :{ rx : xx, tx : xx } }
        #  {2 :{ rx : xx, tx : xx } }
        #        ・・・・
        for msg in self.rcv_msgs:
            for stats in msg.body:
                result[stats.port_no] = {'rx': stats.rx_packets,
                                         'tx': stats.tx_packets}
        return result

    # パケットイン（tester/taregetから）メッセージが期待通りのものかチェック
    def _test_flow_matching_check(self, pkt):
        # 1.ログ出力
        # EGRESS＝xxx PACKET_IN=xxx
        【ログ出力】self.logger.debug("egress:[%s]", packet.Packet(pkt.get(KEY_EGRESS)))
        【ログ出力】self.logger.debug("packet_in:[%s]",
                          packet.Packet(pkt.get(KEY_PKT_IN)))

        # receive a PacketIn message.
        # 2.パケットインを受信するまで待つ（パケット送信に対する返却を期待）
        #   →タイムアウト時は TIMEOUT を返却
        try:
            self._wait() #-------------wait-------------
        except TestTimeout:
            return TIMEOUT

        # 3.メッセージ長が１の場合assert
        assert len(self.rcv_msgs) == 1
        # 4.メッセージ取得→パケットインでなければエラー
        msg = self.rcv_msgs[0]
        assert isinstance(msg, ofproto_v1_3_parser.OFPPacketIn)
        # 5.ログ出力（DPID＝xxx）
        【ログ出力】self.logger.debug("dpid=%s : receive_packet[%s]",
                          dpid_lib.dpid_to_str(msg.datapath.id),
                          packet.Packet(msg.data))

        # check the SW which sended PacketIn and output packet.
        pkt_in_src_model = (self.tester_sw if KEY_EGRESS in pkt
                            else self.target_sw)
        model_pkt = (pkt[KEY_EGRESS] if KEY_EGRESS in pkt
                     else pkt[KEY_PKT_IN])

        # 6.期待する結果と同じかチェック
        #　　メッセージのDPIDは期待通りか？
        #　　メッセージのパケットイン理由は期待通りか？
        #　　メッセージのデータ内容は期待通りか？
        if msg.datapath.id != pkt_in_src_model.dp.id:
            pkt_type = 'packet-in'
            err_msg = 'SW[dpid=%s]' % dpid_lib.dpid_to_str(msg.datapath.id)
        elif msg.reason != ofproto_v1_3.OFPR_ACTION:
            pkt_type = 'packet-in'
            err_msg = 'OFPPacketIn[reason=%d]' % msg.reason
        elif repr(msg.data) != repr(model_pkt):
            pkt_type = 'packet'
            #★
            err_msg = self._diff_packets(packet.Packet(model_pkt),
                                         packet.Packet(msg.data))
        else:
            return TEST_OK

        raise TestFailure(self.state, pkt_type=pkt_type,
                          detail=err_msg)

    #★
    def _test_no_pktin_reason_check(self, test_type,
                                    target_pkt_count, tester_pkt_count):
        before_target_receive = target_pkt_count[0][TARGET_RECEIVE_PORT]['rx']
        before_target_send = target_pkt_count[0][TARGET_SENDER_PORT]['tx']
        before_tester_receive = tester_pkt_count[0][TESTER_RECEIVE_PORT]['rx']
        before_tester_send = tester_pkt_count[0][TESTER_SENDER_PORT]['tx']
        after_target_receive = target_pkt_count[1][TARGET_RECEIVE_PORT]['rx']
        after_target_send = target_pkt_count[1][TARGET_SENDER_PORT]['tx']
        after_tester_receive = tester_pkt_count[1][TESTER_RECEIVE_PORT]['rx']
        after_tester_send = tester_pkt_count[1][TESTER_SENDER_PORT]['tx']

        if after_tester_send == before_tester_send:
            log_msg = 'no change in tx_packets on tester.'
        elif after_target_receive == before_target_receive:
            log_msg = 'no change in rx_packtes on target.'
        elif test_type == KEY_EGRESS:
            if after_target_send == before_target_send:
                log_msg = 'no change in tx_packets on target.'
            elif after_tester_receive == before_tester_receive:
                log_msg = 'no change in rx_packets on tester.'
            else:
                log_msg = 'increment in rx_packets in tester.'
        else:
            assert test_type == KEY_PKT_IN
            log_msg = 'no packet-in.'

        raise TestFailure(self.state, detail=log_msg)

    # テーブルID毎に「検索の回数」「マッチ回数」を取得
    def _test_get_match_count(self):
        # 1.table統計を送信（ターゲットSWに対して）
        xid = self.target_sw.send_table_stats()
        # 2.送信済みxidリストにxidを追加
        self.send_msg_xids.append(xid)
        self._wait() #-------------wait-------------
        result = {}
        # 3.「検索の回数」「マッチ回数」を取得
        for msg in self.rcv_msgs:
            for stats in msg.body:
                result[stats.table_id] = {'lookup': stats.lookup_count,
                                          'matched': stats.matched_count}
        return result

    # バリア送信
    def _test_send_barrier(self):
        # Wait OFPBarrierReply.

        # １. メッセージ送信　＆　xidをアペンド
        xid = self.tester_sw.send_barrier_request()
        self.send_msg_xids.append(xid)

        # ２. 待ち
        self._wait() #-------------wait-------------

        # ３. 結果を確認
        assert len(self.rcv_msgs) == 1
        msg = self.rcv_msgs[0]
        assert isinstance(msg, ofproto_v1_3_parser.OFPBarrierReply)

    # テーブルID毎に「検索の回数」「マッチ回数」を取得
    def _test_flow_unmatching_check(self, before_stats, pkt):
        # Check matched packet count.
        # 1. テーブル統計（検索回数/マッチ回数)を取得
        rcv_msgs = self._test_get_match_count()

        # 2. pkt辞書の　KEY_TBL_MISS　キー のvalueは
        #    テーブルIDの辞書となっているため、ループさせる。
        lookup = False
        for target_tbl_id in pkt[KEY_TBL_MISS]:
            # テーブルID xx の （検索回数/マッチ回数)を取得　(テスト前)
            before = before_stats[target_tbl_id]
            # テーブルID xx の （検索回数/マッチ回数)を取得　(テスト後)
            after = rcv_msgs[target_tbl_id]
            # 検索回数は　増えて　マッチ回数は　増えていない　ならTrueを設定
            if before['lookup'] < after['lookup']:
                lookup = True
                if before['matched'] < after['matched']:
                    raise TestFailure(self.state)
        # lookupがFalse（検索回数が増えなかったり、マッチ回数が増えたり）
        # した場合テストエラー
        if not lookup:
            raise TestError(self.state)


    # パケットを送信（KEY_INGRESS定義のパケットを送信する）
    def _one_time_packet_send(self, pkt):
        # １. メッセージ送信　＆　xidをアペンド
        【ログ出力】self.logger.debug("send_packet:[%s]", packet.Packet(pkt[KEY_INGRESS]))
        xid = self.tester_sw.send_packet_out(pkt[KEY_INGRESS])
        self.send_msg_xids.append(xid)

    # 連続パケット送信
    def _continuous_packet_send(self, pkt):
        # ingress_event が None で　なかったら　エラー
        assert self.ingress_event is None

        # pkt辞書から様々な要素を取得
        pkt_text = pkt[KEY_PACKETS]['packet_text']
        pkt_bin = pkt[KEY_PACKETS]['packet_binary']
        pktps = pkt[KEY_PACKETS][KEY_PKTPS]
        duration_time = pkt[KEY_PACKETS][KEY_DURATION_TIME]
        randomize = pkt[KEY_PACKETS]['randomize']

        【ログ出力】self.logger.debug("send_packet:[%s]", packet.Packet(pkt_bin))
        【ログ出力】self.logger.debug("pktps:[%d]", pktps)
        【ログ出力】self.logger.debug("duration_time:[%d]", duration_time)


        arg = {'packet_text': pkt_text,
               'packet_binary': pkt_bin,
               'thread_counter': 0,
               'dot_span': int(CONTINUOUS_PROGRESS_SPAN /
                               CONTINUOUS_THREAD_INTVL),
               'packet_counter': float(0),
               'packet_counter_inc': pktps * CONTINUOUS_THREAD_INTVL,
               'randomize': randomize}

        try:
            #hub.Event()とは？★
            # ingress_event に hub.Event()を格納
            # →ingress_event に 値が存在
            #  　→　連続印加中！
            self.ingress_event = hub.Event()
            #パケットをスレッドで送信（↓のメソッドへ）
            tid = hub.spawn(self._send_packet_thread, arg)
            #★ここら辺も
            #hub.Event()
            #hub.spawn()
            #ingress_threads
            #ingress_event
            #thread_msg
            self.ingress_threads.append(tid)
            #★ここら辺も
            self.ingress_event.wait(duration_time)
            # thread_msg　＝　None　だったらraise処理
            # raise★
            if self.thread_msg is not None:
                raise self.thread_msg  # pylint: disable=E0702
        finally:
            #stdout ★
            sys.stdout.write("\r\n")
            sys.stdout.flush()

    # パケット連続送信
    def _send_packet_thread(self, arg):
        """ Send several packets continuously. """
        if self.ingress_event is None or self.ingress_event._cond:
            return

        # display dots to express progress of sending packets
        #stdout ★
        #★
        if not arg['thread_counter'] % arg['dot_span']:
            sys.stdout.write(".")
            sys.stdout.flush()

        arg['thread_counter'] += 1

        # pile up float values and
        # use integer portion as the number of packets this thread sends

        # packet_counter　は　パケットカウンター inc を追加していく
        arg['packet_counter'] += arg['packet_counter_inc']
        count = int(arg['packet_counter'])
        # ★
        arg['packet_counter'] -= count

        # インターバルだけhub.sleep★
        hub.sleep(CONTINUOUS_THREAD_INTVL)

        # さらにサブスレッドを起こす？
        tid = hub.spawn(self._send_packet_thread, arg)
        self.ingress_threads.append(tid)
        # スレッド切り替え？★　hub.sleep(0)
        hub.sleep(0)

        # count回数だけ繰り返し

        for _ in range(count):
            # randomize があるばあい
            if arg['randomize']:
                #メッセージをあれこれする★→シリアライズ
                msg = eval('/'.join(arg['packet_text']))
                msg.serialize()
                data = msg.data
            else:
                data = arg['packet_binary']
            try:
                #メッセージ送信（テスターに対して送信）
                self.tester_sw.send_packet_out(data)
            except Exception as err:
                self.thread_msg = err
                self.ingress_event.set()
                break

    # フローを比較
    #　→フロー設定時に、問題なくフローが設定できているか
    #　　チェックするために使う
    def _compare_flow(self, stats1, stats2):

        #フローマッチフィールドの再構築
        def __reasm_match(match):
            """ reassemble match_fields. """
            #マスク長を定義
            #通常はマッチフィールドの長さ（bits）x８だが、
            #仕様書の定義上そうではないものがいくつかあるため、
            #ここで定義する。
            mask_lengths = {'vlan_vid': 12 + 1,
                            'ipv6_exthdr': 9}
            #match_field(最終的に返却するリスト)を定義
            match_fields = list()
            for key, united_value in match.iteritems():
                #united_valueが　タプル　の場合
                if isinstance(united_value, tuple):
                    #　value と mask に文理
                    (value, mask) = united_value
                    # look up oxm_fields.TypeDescr to get mask length.
                    #  ofproto_v1_3.oxm_typeから順番に
                    #  open flow basic を取得
                    for ofb in ofproto_v1_3.oxm_types:
                        if ofb.name == key:
                            #  open flow basic の　名前　が key と一致したら
                            # create all one bits mask
                            # 1. マスク長を取得して
                            mask_len = mask_lengths.get(
                                key, ofb.type.size * 8)
                            # 2. all_one_bits を生成
                            all_one_bits = 2 ** mask_len - 1
                            # convert mask to integer
                            # 3. int型にmaskを変換
                            mask_bytes = ofb.type.from_user(mask)
                            oxm_mask = int(binascii.hexlify(mask_bytes), 16)
                            # 4. all one bits ならマスクを除去
                            # when mask is all one bits, remove mask
                            if oxm_mask & all_one_bits == all_one_bits:
                                united_value = value
                            # 5. all zero bits ならNone
                            # when mask is all zero bits, remove field.
                            elif oxm_mask & all_one_bits == 0:
                                united_value = None
                            break
                # マスクがオールゼロ　ではない場合に、match_fieldに値を代入
                if united_value is not None:
                    #マッチフィールドに key, united_valueを代入
                    # ex.
                    # key = 'in_port', united_value = 3
                    # key = 'vlan_vid', united_value = (xxxx, 0000fff)
                    # key = 'vlan_vid', united_value = None ※ mask = all zero
                    # key = 'vlan_vid', united_value = xxxx ※ mask = all one
                    #     ↑　これらを　list にタプル形式として　足していく
                    # [(key, united_value),(key, united_value),(key, united_value)]
                    match_fields.append((key, united_value))
            return match_fields

        # フローエントリのアトリビュートリストを定義
        attr_list = ['cookie', 'priority', 'hard_timeout', 'idle_timeout',
                     'table_id', 'instructions', 'match']
        # リストを回して、statsから値を取得していく。。cookie->priority->。。。
        for attr in attr_list:
            value1 = getattr(stats1, attr)
            value2 = getattr(stats2, attr)
            if attr == 'instructions':
                value1 = sorted(value1)
                value2 = sorted(value2)
            elif attr == 'match':
                value1 = sorted(__reasm_match(value1))
                value2 = sorted(__reasm_match(value2))
            #値が異なってしまった場合
            if str(value1) != str(value2):
                flow_stats = []
                for attr in attr_list:
                    #flow_statsにアペンドして、falseをリターン
                    flow_stats.append('%s=%s' % (attr, getattr(stats1, attr)))
                return False, 'flow_stats(%s)' % ','.join(flow_stats)
        return True, None

    def _compare_meter(self, stats1, stats2):
        """compare the message used to install and the message got from
           the switch."""
        attr_list = ['flags', 'meter_id', 'bands']
        for attr in attr_list:
            value1 = getattr(stats1, attr)
            value2 = getattr(stats2, attr)
            if str(value1) != str(value2):
                meter_stats = []
                for attr in attr_list:
                    meter_stats.append('%s=%s' % (attr, getattr(stats1, attr)))
                return False, 'meter_stats(%s)' % ','.join(meter_stats)
        return True, None

    def _compare_group(self, stats1, stats2):
        attr_list = ['type', 'group_id', 'buckets']
        for attr in attr_list:
            value1 = getattr(stats1, attr)
            value2 = getattr(stats2, attr)
            if str(value1) != str(value2):
                group_stats = []
                for attr in attr_list:
                    group_stats.append('%s=%s' % (attr, getattr(stats1, attr)))
                return False, 'group_stats(%s)' % ','.join(group_stats)
            return True, None

    # diffパケット（パケットデータを比較）
    # model_pkt 期待するパケットデータのイメージ
    # rcv_pkt 　実際のパケットデータのイメージ
    #          　→ハンドラで受信する ev.msg の
    #               ev.msg.data のこと
    #                 ↑どんなクラス構造？protocols?get_protocols?
    def _diff_packets(self, model_pkt, rcv_pkt):
        # 1. msgを初期化
        msg = []
        # 2. 受信パケットを順番に解析
        #　　→protocolsリストには、Packetインスタンス化時に格納された
        #　　　rcv_pktに含まれるプロトコル一覧が入っている
        for rcv_p in rcv_pkt.protocols:
            # rcv_p 任意のプロトコル
            # プロトコルのタイプが 文字列　でないばあい
            #　→すなわち、パケットライブラリに存在するプロトコルの場合
            if type(rcv_p) != str:
                # 今度はモデルパケット側から、同じタイプのプロトコルインスタンスを取得する
                # 　→　取得結果＝model_protocols
                model_protocols = model_pkt.get_protocols(type(rcv_p))
                # model_protocolsの長さが　１　だったら
                if len(model_protocols) == 1:
                    # 長さは１なので、当然[0]のみを取得
                    model_p = model_protocols[0]
                    # diffを空リストで定義
                    diff = []
                    # プロトコルインスタンス辞書　をループ処理
                    for attr in rcv_p.__dict__:
                        # _ で始まっている場合(隠蔽)は　ループ継続
                        if attr.startswith('_'):
                            continue
                        # 呼び出し可能なら　継続（通常）
                        if callable(attr):
                            continue#               ↓受信パケットの属性★
                        if hasattr(rcv_p.__class__, attr):
                            continue
                        # attr が  rcv_p.__class__ に存在するかチェック
                        # 比較処理を実施（文字列化）
                        rcv_attr = repr(getattr(rcv_p, attr))
                        model_attr = repr(getattr(model_p, attr))
                        if rcv_attr != model_attr:
                            diff.append('%s=%s' % (attr, rcv_attr))
                    if diff:
                        msg.append('%s(%s)' %
                                   (rcv_p.__class__.__name__,
                                    ','.join(diff)))
                # 取得できなかった場合（長さが　２　のときは考慮しない）
                else:
                    # 入手値 が None　或いは
                    # 入手値（str化） の中に　str(rcv_p)が存在しない場合
                    #　→要は、受信したパケットに含まれるプロトコルが
                    #　　期待プロトコルに存在しない場合にメッセージにアペンド
                    if (not model_protocols or
                            not str(rcv_p) in str(model_protocols)):
                        msg.append(str(rcv_p))
            #　→すなわち、パケットライブラリに存在しないプロトコルの場合
            else:
                model_p = ''
                # モデルパケットのプロトコルを回して、
                for p in model_pkt.protocols:
                    # strのタイプを見つけたら、
                    # ★モデルパケットのプロトコルの最初のプロトコルのみ、常に検索されるのでは？
                    if type(p) == str:
                        # model_pに入れる
                        model_p = p
                        break
                # ちがったら、メッセージにアペンド
                if model_p != rcv_p:
                    msg.append('str(%s)' % repr(rcv_p))
        # msgをリターン（エラー有無に関わらず）
        if msg:
            return '/'.join(msg)
        else:
            return ('Encounter an error during packet comparison.'
                    ' it is malformed.')

    # フロー統計（指定クッキーのみ）を取得
    def _test_get_throughput(self):
        # １. メッセージ送信　＆　xidをアペンド
        xid = self.tester_sw.send_flow_stats()
        self.send_msg_xids.append(xid)

        # ２. 待ち

        self._wait() #-------------wait-------------

        # ３. 結果を確認（統計を返却）
        assert len(self.rcv_msgs) == 1
        flow_stats = self.rcv_msgs[0].body
        【ログ出力】self.logger.debug(flow_stats)
        result = {}
        for stat in flow_stats:
            if stat.cookie != THROUGHPUT_COOKIE:
                continue
            result[str(stat.match)] = (stat.byte_count, stat.packet_count)
        return (time.time(), result)

    # スループットチェック
    def _test_throughput_check(self, throughputs, start, end):
        msgs = []
        # 1 処理時間取得
        elapsed_sec = end[0] - start[0]

        # 2 期待するスループットを取得
        for throughput in throughputs:
            # 3 マッチを取得★
            match = str(throughput[KEY_FLOW].match)

            # 4 期待するスループットを取得
            # get oxm_fields of OFPMatch
            fields = dict(throughput[KEY_FLOW].match._fields2)

            # 5 エラーチェック★
            if match not in start[1] or match not in end[1]:
                raise TestError(self.state, match=match)

            # 6 上昇値（バイト、パケット）を取得
            increased_bytes = end[1][match][0] - start[1][match][0]
            increased_packets = end[1][match][1] - start[1][match][1]

            # 7 テストファイルからスループットの期待値を取得
            # 　単位は（PKTPS、KBPS）
            if throughput[KEY_PKTPS]:
                key = KEY_PKTPS
                conv = 1
                measured_value = increased_packets
                unit = 'pktps'
            elif throughput[KEY_KBPS]:
                key = KEY_KBPS
                conv = 1024 / 8  # Kilobits -> bytes
                measured_value = increased_bytes
                unit = 'kbps'
            else:
                raise RyuException(
                    'An invalid key exists that is neither "%s" nor "%s".'
                    % (KEY_KBPS, KEY_PKTPS))

            # 8 送信したであろうパケット数/キロバイト数を取得（期待値）]
            expected_value = throughput[key] * elapsed_sec * conv
            # 9 多少の誤差は発生するため、マージン値を取得（１０％までなら許容）
            margin = expected_value * THROUGHPUT_THRESHOLD
            【ログ出力】self.logger.debug("measured_value:[%s]", measured_value)
            【ログ出力】self.logger.debug("expected_value:[%s]", expected_value)
            【ログ出力】self.logger.debug("margin:[%s]", margin)
            # 10 計測値　と　期待値　の差分の　絶対値　を取得
            #　　→誤差がマージン値を越えていた場合
            if math.fabs(measured_value - expected_value) > margin:
                #メッセージにアペンド
                msgs.append('{0} {1:.2f}{2}'.format(fields,
                            measured_value / elapsed_sec / conv, unit))

        if msgs:
            #メッセージが存在した場合 TESTFailure→スループット値が期待した計測値ではない
            raise TestFailure(self.state, detail=', '.join(msgs))

    # 待ち処理＠OFメッセージを受信するまで待ち続ける
    # 　statsリクエスト送信時等に利用する
    def _wait(self):
        """ Wait until specific OFP message received
             or timer is exceeded. """

        #waiterがNoneであることを確認
        assert self.waiter is None

        #waiterを設定
        self.waiter = hub.Event()
        #レシーブメッセージを初期化
        self.rcv_msgs = []
        timeout = False

        #タイマーを設定
        timer = hub.Timeout(WAIT_TIMER)
        try:
            # waiter による waitを実行
            self.waiter.wait()
        # タイムアウト発生時は、RYUException（内部エラー）を出力する
        except hub.Timeout as t:
            if t is not timer:
                raise RyuException('Internal error. Not my timeout.')
            timeout = True
        finally:
            timer.cancel()

        #waiterを初期化（None化）
        self.waiter = None

        #タイムアウトだった場合、raise実施（タイムアウト）
        if timeout:
            raise TestTimeout(self.state)
        #エラーメッセージ受信の場合、raise実施（エラー受信）
        if (self.rcv_msgs and isinstance(
                self.rcv_msgs[0], ofproto_v1_3_parser.OFPErrorMsg)):
            raise TestReceiveError(self.state, self.rcv_msgs[0])

    #統計リプライのハンドラ
    @set_ev_cls([ofp_event.EventOFPFlowStatsReply,
                 ofp_event.EventOFPMeterConfigStatsReply,
                 ofp_event.EventOFPTableStatsReply,
                 ofp_event.EventOFPPortStatsReply,
                 ofp_event.EventOFPGroupDescStatsReply],
                handler.MAIN_DISPATCHER)
    def stats_reply_handler(self, ev):
        # keys: stats reply event classes
        # values: states in which the events should be processed
        event_states = {
            ofp_event.EventOFPFlowStatsReply:
                [STATE_FLOW_EXIST_CHK,
                 STATE_THROUGHPUT_FLOW_EXIST_CHK,
                 STATE_GET_THROUGHPUT],
            ofp_event.EventOFPMeterConfigStatsReply:
                [STATE_METER_EXIST_CHK],
            ofp_event.EventOFPTableStatsReply:
                [STATE_GET_MATCH_COUNT,
                 STATE_FLOW_UNMATCH_CHK],
            ofp_event.EventOFPPortStatsReply:
                [STATE_TARGET_PKT_COUNT,
                 STATE_TESTER_PKT_COUNT],
            ofp_event.EventOFPGroupDescStatsReply:
                [STATE_GROUP_EXIST_CHK]
        }
        # state が event_states辞書に含まれている場合、処理を開始
        # →関係のないメッセージの場合はスルー
        if self.state in event_states[ev.__class__]:
            # 送信済みxidに含まれている場合、rcv_msgsにアペンド
            if self.waiter and ev.msg.xid in self.send_msg_xids:
                # rcv_msgsアペンド
                self.rcv_msgs.append(ev.msg)
                if not ev.msg.flags & ofproto_v1_3.OFPMPF_REPLY_MORE:
                    self.waiter.set()
                    hub.sleep(0)

    @set_ev_cls(ofp_event.EventOFPBarrierReply, handler.MAIN_DISPATCHER)
    def barrier_reply_handler(self, ev):
        state_list = [STATE_INIT_FLOW,
                      STATE_INIT_THROUGHPUT_FLOW,
                      STATE_INIT_METER,
                      STATE_INIT_GROUP,
                      STATE_FLOW_INSTALL,
                      STATE_THROUGHPUT_FLOW_INSTALL,
                      STATE_METER_INSTALL,
                      STATE_GROUP_INSTALL,
                      STATE_SEND_BARRIER]
        if self.state in state_list:
            # 送信済みxidに含まれている場合、rcv_msgsにアペンド
            if self.waiter and ev.msg.xid in self.send_msg_xids:
                # rcv_msgsアペンド
                self.rcv_msgs.append(ev.msg)
                self.waiter.set()
                hub.sleep(0)

    @set_ev_cls(ofp_event.EventOFPPacketIn, handler.MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        # パケットインの場合は、送信済みxid等は関係なし
        state_list = [STATE_FLOW_MATCH_CHK]
        if self.state in state_list:
            if self.waiter:
                # rcv_msgsアペンド
                self.rcv_msgs.append(ev.msg)
                self.waiter.set()
                hub.sleep(0)

    @set_ev_cls(ofp_event.EventOFPErrorMsg, [handler.HANDSHAKE_DISPATCHER,
                                             handler.CONFIG_DISPATCHER,
                                             handler.MAIN_DISPATCHER])
    def error_msg_handler(self, ev):
        # 送信済みxidに含まれている場合、rcv_msgsにアペンド
        if ev.msg.xid in self.send_msg_xids:
            # rcv_msgsアペンド
            self.rcv_msgs.append(ev.msg)
            if self.waiter:
                self.waiter.set()
                hub.sleep(0)


class OpenFlowSw(object):
    def __init__(self, dp, logger):
        super(OpenFlowSw, self).__init__()
        self.dp = dp
        self.logger = logger

    def send_msg(self, msg):
        if isinstance(self.dp, DummyDatapath):
            raise TestError(STATE_DISCONNECTED)
        msg.xid = None
        self.dp.set_xid(msg)
        self.dp.send_msg(msg)
        return msg.xid

    def add_flow(self, in_port=None, out_port=None):
        """ Add flow. """
        ofp = self.dp.ofproto
        parser = self.dp.ofproto_parser

        match = parser.OFPMatch(in_port=in_port)
        max_len = (0 if out_port != ofp.OFPP_CONTROLLER
                   else ofp.OFPCML_MAX)
        actions = [parser.OFPActionOutput(out_port, max_len)]
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS,
                                             actions)]
        mod = parser.OFPFlowMod(self.dp, cookie=0,
                                command=ofp.OFPFC_ADD,
                                match=match, instructions=inst)
        return self.send_msg(mod)

    def del_flows(self, cookie=0):
        """ Delete all flow except default flow. """
        ofp = self.dp.ofproto
        parser = self.dp.ofproto_parser
        cookie_mask = 0
        if cookie:
            cookie_mask = 0xffffffffffffffff
        mod = parser.OFPFlowMod(self.dp,
                                cookie=cookie,
                                cookie_mask=cookie_mask,
                                table_id=ofp.OFPTT_ALL,
                                command=ofp.OFPFC_DELETE,
                                out_port=ofp.OFPP_ANY,
                                out_group=ofp.OFPG_ANY)
        return self.send_msg(mod)

    def del_meters(self):
        """ Delete all meter entries. """
        ofp = self.dp.ofproto
        parser = self.dp.ofproto_parser
        mod = parser.OFPMeterMod(self.dp,
                                 command=ofp.OFPMC_DELETE,
                                 flags=0,
                                 meter_id=ofp.OFPM_ALL)
        return self.send_msg(mod)

    def del_groups(self):
        ofp = self.dp.ofproto
        parser = self.dp.ofproto_parser
        mod = parser.OFPGroupMod(self.dp,
                                 command=ofp.OFPGC_DELETE,
                                 type_=0,
                                 group_id=ofp.OFPG_ALL)
        return self.send_msg(mod)

    def send_barrier_request(self):
        """ send a BARRIER_REQUEST message."""
        parser = self.dp.ofproto_parser
        req = parser.OFPBarrierRequest(self.dp)
        return self.send_msg(req)

    def send_port_stats(self):
        """ Get port stats."""
        ofp = self.dp.ofproto
        parser = self.dp.ofproto_parser
        flags = 0
        req = parser.OFPPortStatsRequest(self.dp, flags, ofp.OFPP_ANY)
        return self.send_msg(req)

    def send_flow_stats(self):
        """ Get all flow. """
        ofp = self.dp.ofproto
        parser = self.dp.ofproto_parser
        req = parser.OFPFlowStatsRequest(self.dp, 0, ofp.OFPTT_ALL,
                                         ofp.OFPP_ANY, ofp.OFPG_ANY,
                                         0, 0, parser.OFPMatch())
        return self.send_msg(req)

    def send_meter_config_stats(self):
        """ Get all meter. """
        parser = self.dp.ofproto_parser
        stats = parser.OFPMeterConfigStatsRequest(self.dp)
        return self.send_msg(stats)

    def send_group_desc_stats(self):
        parser = self.dp.ofproto_parser
        stats = parser.OFPGroupDescStatsRequest(self.dp)
        return self.send_msg(stats)

    def send_table_stats(self):
        """ Get table stats. """
        parser = self.dp.ofproto_parser
        req = parser.OFPTableStatsRequest(self.dp, 0)
        return self.send_msg(req)

    def send_packet_out(self, data):
        """ send a PacketOut message."""
        ofp = self.dp.ofproto
        parser = self.dp.ofproto_parser
        actions = [parser.OFPActionOutput(TESTER_SENDER_PORT)]
        out = parser.OFPPacketOut(
            datapath=self.dp, buffer_id=ofp.OFP_NO_BUFFER,
            data=data, in_port=ofp.OFPP_CONTROLLER, actions=actions)
        return self.send_msg(out)


class TestPatterns(dict):
    """ List of Test class objects. """
    def __init__(self, test_dir, logger):
        super(TestPatterns, self).__init__()
        self.logger = logger
        #テストパターンのインスタンス時にget_testを呼ぶ
        #　→　テストファイルがインスタンス化
        #　　→　テスト　もインスタンス化
        #　　　　→　このときにパース処理も同時実行される。
        # Parse test pattern from test files.
        self._get_tests(test_dir)

    def _get_tests(self, path):
        if not os.path.exists(path):
            msg = INVALID_PATH % {'path': path}
            self.logger.warning(msg)
            return

        if os.path.isdir(path):  # Directory
            for test_path in os.listdir(path):
                test_path = path + (test_path if path[-1:] == '/'
                                    else '/%s' % test_path)
                self._get_tests(test_path)

        elif os.path.isfile(path):  # File
            (dummy, ext) = os.path.splitext(path)
            if ext == '.json':
                test = TestFile(path, self.logger)
                self[test.description] = test


class TestFile(stringify.StringifyMixin):
    """Test File object include Test objects."""
    def __init__(self, path, logger):
        super(TestFile, self).__init__()
        self.logger = logger
        self.description = None
        self.tests = []
        # TestFileインスタンス化時にテスト取得も実施
        self._get_tests(path)

    def _get_tests(self, path):
        # path を元にファイルオープン
        with open(path, 'rb') as fhandle:
            buf = fhandle.read()
            try:
                # jsonファイルのリストを取得
                json_list = json.loads(buf)
                for test_json in json_list:
                    if isinstance(test_json, unicode):
                        # テストファイルのデスク利付書んを取得
                        self.description = test_json
                    else:
                        # テスト一覧（リスト）にパースしたテストインスタンスを
                        # 格納する
                        self.tests.append(Test(test_json))
            except (ValueError, TypeError) as e:
                result = (TEST_FILE_ERROR %
                          {'file': path, 'detail': e.message})
                self.logger.warning(result)


class Test(stringify.StringifyMixin):
    # 初期化
    def __init__(self, test_json):
        super(Test, self).__init__()
        # 初期化時にテストのパース（jsonの読み込み）藻実施
        (self.description,
         self.prerequisite,
         self.tests) = self._parse_test(test_json)

    def _parse_test(self, buf):
        def __test_pkt_from_json(test):
            data = eval('/'.join(test))
            data.serialize()
            return str(data.data)

        # parse 'description'
        description = buf.get(KEY_DESC)

        # parse 'prerequisite'
        prerequisite = []
        if KEY_PREREQ not in buf:
            raise ValueError('a test requires a "%s" block' % KEY_PREREQ)
        allowed_mod = [KEY_FLOW, KEY_METER, KEY_GROUP]
        for flow in buf[KEY_PREREQ]:
            key, value = flow.popitem()
            if key not in allowed_mod:
                raise ValueError(
                    '"%s" block allows only the followings: %s' % (
                        KEY_PREREQ, allowed_mod))
            cls = getattr(ofproto_v1_3_parser, key)
            msg = cls.from_jsondict(value, datapath=DummyDatapath())
            msg.version = ofproto_v1_3.OFP_VERSION
            msg.msg_type = msg.cls_msg_type
            msg.xid = 0
            prerequisite.append(msg)

        # parse 'tests'
        tests = []
        if KEY_TESTS not in buf:
            raise ValueError('a test requires a "%s" block.' % KEY_TESTS)

        for test in buf[KEY_TESTS]:
            if len(test) != 2:
                raise ValueError(
                    '"%s" block requires "%s" field and one of "%s" or "%s"'
                    ' or "%s" field.' % (KEY_TESTS, KEY_INGRESS, KEY_EGRESS,
                                         KEY_PKT_IN, KEY_TBL_MISS))
            test_pkt = {}
            # parse 'ingress'
            if KEY_INGRESS not in test:
                raise ValueError('a test requires "%s" field.' % KEY_INGRESS)
            if isinstance(test[KEY_INGRESS], list):
                test_pkt[KEY_INGRESS] = __test_pkt_from_json(test[KEY_INGRESS])
            elif isinstance(test[KEY_INGRESS], dict):
                test_pkt[KEY_PACKETS] = {
                    'packet_text': test[KEY_INGRESS][KEY_PACKETS][KEY_DATA],
                    'packet_binary': __test_pkt_from_json(
                        test[KEY_INGRESS][KEY_PACKETS][KEY_DATA]),
                    KEY_DURATION_TIME: test[KEY_INGRESS][KEY_PACKETS].get(
                        KEY_DURATION_TIME, DEFAULT_DURATION_TIME),
                    KEY_PKTPS: test[KEY_INGRESS][KEY_PACKETS].get(
                        KEY_PKTPS, DEFAULT_PKTPS),
                    'randomize': True in [
                        line.find('randint') != -1
                        for line in test[KEY_INGRESS][KEY_PACKETS][KEY_DATA]]}
            else:
                raise ValueError('invalid format: "%s" field' % KEY_INGRESS)
            # parse 'egress' or 'PACKET_IN' or 'table-miss'
            if KEY_EGRESS in test:
                if isinstance(test[KEY_EGRESS], list):
                    test_pkt[KEY_EGRESS] = __test_pkt_from_json(
                        test[KEY_EGRESS])
                elif isinstance(test[KEY_EGRESS], dict):
                    throughputs = []
                    for throughput in test[KEY_EGRESS][KEY_THROUGHPUT]:
                        one = {}
                        mod = {'match': {'OFPMatch': throughput[KEY_MATCH]}}
                        cls = getattr(ofproto_v1_3_parser, KEY_FLOW)
                        msg = cls.from_jsondict(
                            mod, datapath=DummyDatapath(),
                            cookie=THROUGHPUT_COOKIE,
                            priority=THROUGHPUT_PRIORITY)
                        one[KEY_FLOW] = msg
                        one[KEY_KBPS] = throughput.get(KEY_KBPS)
                        one[KEY_PKTPS] = throughput.get(KEY_PKTPS)
                        if not bool(one[KEY_KBPS]) != bool(one[KEY_PKTPS]):
                            raise ValueError(
                                '"%s" requires either "%s" or "%s".' % (
                                    KEY_THROUGHPUT, KEY_KBPS, KEY_PKTPS))
                        throughputs.append(one)
                    test_pkt[KEY_THROUGHPUT] = throughputs
                else:
                    raise ValueError('invalid format: "%s" field' % KEY_EGRESS)
            elif KEY_PKT_IN in test:
                test_pkt[KEY_PKT_IN] = __test_pkt_from_json(test[KEY_PKT_IN])
            elif KEY_TBL_MISS in test:
                test_pkt[KEY_TBL_MISS] = test[KEY_TBL_MISS]

            tests.append(test_pkt)

        return (description, prerequisite, tests)


class DummyDatapath(object):
    def __init__(self):
        self.ofproto = ofproto_v1_3
        self.ofproto_parser = ofproto_v1_3_parser

    def set_xid(self, _):
        pass

    def send_msg(self, _):
        pass
