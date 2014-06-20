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

import inspect
import struct

from . import packet_base
from . import ethernet

    #使い方
    #　ポイント：initした瞬間にデータ→インスタンスに変換されて
    #　　　　　　protocolsリストにインスタンス群が格納される仕組み。
    #
    # 1. parseしたいデータを引数としてPacketインスタンスを作成
    #　　→req_pkt = packet.Packet(msg.data)
    #  　　※init時に自動でparserが呼ばれて、「protocols」リストに
    #        プロトコルクラス群が格納される。
    # 2. get_protocol（プロトコルライブラリ）でxxを入手
    #　　→req_igmp = req_pkt.get_protocol(igmp.igmp)
    #  　　※self.protocolsリストを探して
    #　　　　 見つかったら、インスタンスを返却
    #if req_igmp:
        #if self._querier.dpid == dpid:
            #self._querier.packet_in_handler(req_igmp, msg)
        #else:
            #self._snooper.packet_in_handler(req_pkt, req_igmp, msg)
    #else:
        #self.send_event_to_observers(EventPacketIn(msg))


class Packet(object):
    """A packet decoder/encoder class.

    An instance is used to either decode or encode a single packet.

    *data* is a bytearray to describe a raw datagram to decode.
    When decoding, a Packet object is iteratable.
    Iterated values are protocol (ethernet, ipv4, ...) headers and the payload.
    Protocol headers are instances of subclass of packet_base.PacketBase.
    The payload is a bytearray.  They are iterated in on-wire order.

    *data* should be omitted when encoding a packet.
    """

    #

    #初期化
    # data はバイトアレイ　生のデータグラムを記述するため
    def __init__(self, data=None, protocols=None, parse_cls=ethernet.ethernet):
        super(Packet, self).__init__()
        #dataを入れる
        self.data = data
        #protocolsがNoneなら、
        # リストを入れる
        if protocols is None:
            self.protocols = []
        else:
        # リストでないなら、protocols（引数）を入れる
        #  →リストで入力が可能
            self.protocols = protocols

        # self.dataが存在するなら、
        #  パーサーを実行する（デフォルトはイーサネット）
        if self.data:
            self._parser(parse_cls)

    #初期化時に実行されるパーサー
    def _parser(self, cls):
        # 1. rest_data を入手
        rest_data = self.data
        # 2. clsが存在するなら
        while cls:
            try:
                # 3. クラスのパーサーを実行
                #    → protocol(次の)、パース結果、残りのデータ
                #      　を入手
                proto, cls, rest_data = cls.parser(rest_data)
            except struct.error:
                break
            if proto:
                # 4. 次のプロトコル　が存在するなら
                #    self.protocolsにに追加（初回の追加作業はinit時に実行済み）
                self.protocols.append(proto)
        if rest_data:
            self.protocols.append(rest_data)

    def serialize(self):
        """Encode a packet and store the resulted bytearray in self.data.

        This method is legal only when encoding a packet.
        """
        # 1. data を初期化
        self.data = bytearray()

        # 2. protocols を逆順に並べる
        # ex. ether,ip,tcp,,,,となっていたら
        #     tcp ip ether となる
        r = self.protocols[::-1]
        # i はインクリメントする数値（０、１、２、、、
        # p はrの中身を順番にとりだし
        for i, p in enumerate(r):
        # ↑シーケンスのすべての要素に対してインデックスつきで何らかの処理を行いたい場合
            # パケットライブラリ有りのインスタンスなら
            if isinstance(p, packet_base.PacketBase):
                #最後のループだったら
                if i == len(r) - 1:
                    prev = None
                else:
                #それいがいなら
                #次の要素をprevに代入
                    prev = r[i + 1]
                # p         現在処理しているしているクラスインスタンス
                # prev      次のクラスインスタンス
                # self.data シリアライズ中のデータ
                data = p.serialize(self.data, prev)
                # ↑注目プロトコルについてシリアライズした結果を返却する
            # そうでないなら
            else:
                data = str(p)
            # 完成したdata(xxプロトコルのシリアライズ結果）をself.dataにくっつける
            self.data = data + self.data
             #　空　　　　↓注目プロトコルのシリアライズ結果
             #　空　＋　data
             #　空　＋　data ＋data

    # protoプロトコルを登録するための処理？
    #
    def add_protocol(self, proto):
        """Register a protocol *proto* for this packet.

        # このメソッドはパケットをエンコードする際にのみ有効
        This method is legal only when encoding a packet.

        パケットをシリアライズするとき、このパケットに追加するプロトコル
        （イーサネット、IPv4の、...）ヘッダを登録します。
        プロトコルヘッダはself.serializeを呼び出す前に、
        on-wireの順序をで登録する必要があります。

        When encoding a packet, register a protocol (ethernet, ipv4, ...)
        header to add to this packet.
        Protocol headers should be registered in on-wire order before calling
        self.serialize.
        """
        # 使用例
        ## create a general query.
        # 1. プロトコル毎のインスタンスを生成
        #res_igmp = igmp.igmp(
            #msgtype=igmp.IGMP_TYPE_QUERY,
            #maxresp=igmp.QUERY_RESPONSE_INTERVAL * 10,
            #csum=0,
            #address='0.0.0.0')
        # 1. プロトコル毎のインスタンスを生成
        #res_ipv4 = ipv4.ipv4(
            #total_length=len(ipv4.ipv4()) + len(res_igmp),
            #proto=inet.IPPROTO_IGMP, ttl=1,
            #src='0.0.0.0',
            #dst=igmp.MULTICAST_IP_ALL_HOST)
        # 1. プロトコル毎のインスタンスを生成
        #res_ether = ethernet.ethernet(
            #dst=igmp.MULTICAST_MAC_ALL_HOST,
            #src=self._datapath.ports[ofproto.OFPP_LOCAL].hw_addr,
            #ethertype=ether.ETH_TYPE_IP)
        # 2. パケットインスタンスを生成（空で）
        #res_pkt = packet.Packet()
        # 3. 登録したい順番にプロトコルインスタンスを追加
        #res_pkt.add_protocol(res_ether)
        #res_pkt.add_protocol(res_ipv4)
        #res_pkt.add_protocol(res_igmp)
        # 4. シリアライズを実施
        #res_pkt.serialize()
        # 5. パケット送信
        ## send a general query to the host that sent this message.
            #self._do_packet_out(
                #self._datapath, res_pkt.data, send_port, flood)

        self.protocols.append(proto)

    def get_protocols(self, protocol):
        """Returns a list of protocols that matches to the specified protocol.
        """
        if isinstance(protocol, packet_base.PacketBase):
            protocol = protocol.__class__
        assert issubclass(protocol, packet_base.PacketBase)
        return [p for p in self.protocols if isinstance(p, protocol)]

    def get_protocol(self, protocol):
        """Returns the firstly found protocol that matches to the
        specified protocol.
        """
        result = self.get_protocols(protocol)
        if len(result) > 0:
            return result[0]
        return None

    def __div__(self, trailer):
        self.add_protocol(trailer)
        return self

    def __iter__(self):
        return iter(self.protocols)

    def __getitem__(self, idx):
        return self.protocols[idx]

    def __setitem__(self, idx, item):
        self.protocols[idx] = item

    def __delitem__(self, idx):
        del self.protocols[idx]

    def __len__(self):
        return len(self.protocols)

    def __contains__(self, protocol):
        if (inspect.isclass(protocol) and
                issubclass(protocol, packet_base.PacketBase)):
            return protocol in [p.__class__ for p in self.protocols]
        return protocol in self.protocols

    def __str__(self):
        return ', '.join(repr(protocol) for protocol in self.protocols)
    __repr__ = __str__  # note: str(list) uses __repr__ for elements


# XXX: Hack for preventing recursive import
def _PacketBase__div__(self, trailer):
    pkt = Packet()
    pkt.add_protocol(self)
    pkt.add_protocol(trailer)
    return pkt

packet_base.PacketBase.__div__ = _PacketBase__div__
