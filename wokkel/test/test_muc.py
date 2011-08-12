# Copyright (c) Ralph Meijer.
# See LICENSE for details.

"""
Tests for L{wokkel.muc}
"""

from datetime import datetime
from dateutil.tz import tzutc

from zope.interface import verify

from twisted.trial import unittest
from twisted.internet import defer, task
from twisted.words.xish import domish, xpath
from twisted.words.protocols.jabber.jid import JID
from twisted.words.protocols.jabber.error import StanzaError
from twisted.words.protocols.jabber.xmlstream import TimeoutError, toResponse

from wokkel import data_form, iwokkel, muc
from wokkel.generic import parseXml
from wokkel.test.helpers import XmlStreamStub, TestableStreamManager


NS_MUC_ADMIN = 'http://jabber.org/protocol/muc#admin'

def calledAsync(fn):
    """
    Function wrapper that fires a deferred upon calling the given function.
    """
    d = defer.Deferred()

    def func(*args, **kwargs):
        try:
            result = fn(*args, **kwargs)
        except:
            d.errback()
        else:
            d.callback(result)

    return d, func



class MUCClientTest(unittest.TestCase):
    timeout = 2

    def setUp(self):
        self.clock = task.Clock()
        self.sessionManager = TestableStreamManager(reactor=self.clock)
        self.stub = self.sessionManager.stub
        self.protocol = muc.MUCClient(reactor=self.clock)
        self.protocol.setHandlerParent(self.sessionManager)

        self.roomIdentifier = 'test'
        self.service  = 'conference.example.org'
        self.nick = 'Nick'

        self.occupantJID = JID(tuple=(self.roomIdentifier,
                                      self.service,
                                      self.nick))
        self.roomJID = self.occupantJID.userhostJID()
        self.userJID = JID('test@example.org/Testing')


    def _createRoom(self):
        """
        A helper method to create a test room.
        """
        # create a room
        room = muc.Room(self.roomIdentifier,
                        self.service,
                        self.nick)
        self.protocol._addRoom(room)


    def test_interface(self):
        """
        Do instances of L{muc.MUCClient} provide L{iwokkel.IMUCClient}?
        """
        verify.verifyObject(iwokkel.IMUCClient, self.protocol)


    def test_userJoinedRoom(self):
        """
        Joins by others to a room we're in are passed to userJoinedRoom
        """
        xml = """
            <presence to='%s' from='%s'>
              <x xmlns='http://jabber.org/protocol/muc#user'>
                <item affiliation='member' role='participant'/>
              </x>
            </presence>
        """ % (self.userJID.full(), self.occupantJID.full())

        # create a room
        self._createRoom()

        def userJoinedRoom(room, user):
            self.assertEquals(self.roomIdentifier, room.roomIdentifier,
                              'Wrong room name')
            self.assertTrue(room.inRoster(user), 'User not in roster')

        d, self.protocol.userJoinedRoom = calledAsync(userJoinedRoom)
        self.stub.send(parseXml(xml))
        return d


    def test_receivedSubject(self):
        """
        Subject received from a room we're in are passed to receivedSubject.
        """
        xml = u"""
            <message to='%s' from='%s' type='groupchat'>
              <subject>test</subject>
            </message>
        """ % (self.userJID, self.occupantJID)

        self._createRoom()

        # add user to room
        user = muc.User(self.nick)
        room = self.protocol._getRoom(self.roomJID)
        room.addUser(user)

        def receivedSubject(room, user, subject):
            self.assertEquals('test', subject, "Wrong group chat message")
            self.assertEquals(self.roomIdentifier, room.roomIdentifier,
                              'Wrong room name')
            self.assertEquals(self.nick, user.nick)

        d, self.protocol.receivedSubject = calledAsync(receivedSubject)
        self.stub.send(parseXml(xml))
        return d


    def test_receivedGroupChat(self):
        """
        Messages received from a room we're in are passed to receivedGroupChat.
        """
        xml = u"""
            <message to='test@test.com' from='%s' type='groupchat'>
              <body>test</body>
            </message>
        """ % (self.occupantJID)

        self._createRoom()

        def receivedGroupChat(room, user, message):
            self.assertEquals('test', message.body, "Wrong group chat message")
            self.assertEquals(self.roomIdentifier, room.roomIdentifier,
                              'Wrong room name')

        d, self.protocol.receivedGroupChat = calledAsync(receivedGroupChat)
        self.stub.send(parseXml(xml))
        return d


    def test_receivedGroupChatRoom(self):
        """
        Messages received from the room itself have C{user} set to C{None}.
        """
        xml = u"""
            <message to='test@test.com' from='%s' type='groupchat'>
              <body>test</body>
            </message>
        """ % (self.roomJID)

        self._createRoom()

        def receivedGroupChat(room, user, message):
            self.assertIdentical(None, user)

        d, self.protocol.receivedGroupChat = calledAsync(receivedGroupChat)
        self.stub.send(parseXml(xml))
        return d


    def test_join(self):
        """
        Joining a room waits for confirmation, deferred fires room.
        """

        def cb(room):
            self.assertEquals(self.roomIdentifier, room.roomIdentifier)

        d = self.protocol.join(self.service, self.roomIdentifier, self.nick)
        d.addCallback(cb)

        element = self.stub.output[-1]
        self.assertEquals('presence', element.name, "Need to be presence")
        self.assertNotIdentical(None, element.x, 'No muc x element')

        # send back user presence, they joined
        xml = """
            <presence from='%s@%s/%s'>
              <x xmlns='http://jabber.org/protocol/muc#user'>
                <item affiliation='member' role='participant'/>
              </x>
            </presence>
        """ % (self.roomIdentifier, self.service, self.nick)
        self.stub.send(parseXml(xml))
        return d


    def test_joinHistory(self):
        """
        Passing a history parameter sends a 'maxstanzas' history limit.
        """

        def cb(room):
            self.assertEquals(self.roomIdentifier, room.roomIdentifier)

        d = self.protocol.join(self.service, self.roomIdentifier, self.nick,
                               history=10)
        d.addCallback(cb)

        element = self.stub.output[-1]
        query = "/*/x[@xmlns='%s']/history[@xmlns='%s']" % (muc.NS_MUC,
                                                            muc.NS_MUC)
        result = xpath.queryForNodes(query, element)
        history = result[0]
        self.assertEquals('10', history.getAttribute('maxstanzas'))

        # send back user presence, they joined
        xml = """
            <presence from='%s@%s/%s'>
              <x xmlns='http://jabber.org/protocol/muc#user'>
                <item affiliation='member' role='participant'/>
              </x>
            </presence>
        """ % (self.roomIdentifier, self.service, self.nick)
        self.stub.send(parseXml(xml))
        return d


    def test_joinForbidden(self):
        """
        A forbidden error in response to a join errbacks with L{StanzaError}.
        """

        def cb(error):
            self.assertEquals('forbidden', error.condition,
                              'Wrong muc condition')

        d = self.protocol.join(self.service, self.roomIdentifier, self.nick)
        self.assertFailure(d, StanzaError)
        d.addCallback(cb)

        # send back error, forbidden
        xml = u"""
            <presence from='%s' type='error'>
              <error type='auth'>
                <forbidden xmlns='urn:ietf:params:xml:ns:xmpp-stanzas'/>
              </error>
            </presence>
        """ % (self.occupantJID)
        self.stub.send(parseXml(xml))
        return d


    def test_joinForbiddenFromRoomJID(self):
        """
        An error response to a join sent from the room JID should errback.

        Some service implementations send error stanzas from the room JID
        instead of the JID the join presence was sent to.
        """

        d = self.protocol.join(self.service, self.roomIdentifier, self.nick)
        self.assertFailure(d, StanzaError)

        # send back error, forbidden
        xml = u"""
            <presence from='%s' type='error'>
              <error type='auth'>
                <forbidden xmlns='urn:ietf:params:xml:ns:xmpp-stanzas'/>
              </error>
            </presence>
        """ % (self.roomJID)
        self.stub.send(parseXml(xml))
        return d


    def test_joinBadJID(self):
        """
        Client joining a room and getting a jid-malformed error.
        """

        def cb(error):
            self.assertEquals('jid-malformed', error.condition,
                              'Wrong muc condition')

        d = self.protocol.join(self.service, self.roomIdentifier, self.nick)
        self.assertFailure(d, StanzaError)
        d.addCallback(cb)

        # send back error, bad JID
        xml = u"""
            <presence from='%s' type='error'>
              <error type='modify'>
                <jid-malformed xmlns='urn:ietf:params:xml:ns:xmpp-stanzas'/>
              </error>
            </presence>
        """ % (self.occupantJID)
        self.stub.send(parseXml(xml))
        return d


    def test_joinTimeout(self):
        """
        After not receiving a response to a join, errback with L{TimeoutError}.
        """

        d = self.protocol.join(self.service, self.roomIdentifier, self.nick)
        self.assertFailure(d, TimeoutError)
        self.clock.advance(muc.DEFER_TIMEOUT)
        return d


    def test_leave(self):
        """
        Client leaves a room
        """
        def cb(left):
            self.assertTrue(left, 'did not leave room')

        self._createRoom()
        d = self.protocol.leave(self.roomJID)
        d.addCallback(cb)

        element = self.stub.output[-1]

        self.assertEquals('unavailable', element['type'],
                          'Unavailable is not being sent')

        xml = u"""
            <presence to='%s' from='%s' type='unavailable'/>
        """ % (self.userJID, self.occupantJID)
        self.stub.send(parseXml(xml))
        return d


    def test_userLeftRoom(self):
        """
        Unavailable presence from a participant removes it from the room.
        """

        xml = u"""
            <presence to='%s' from='%s' type='unavailable'/>
        """ % (self.userJID, self.occupantJID)

        # create a room
        self._createRoom()

        # add user to room
        user = muc.User(self.nick)
        room = self.protocol._getRoom(self.roomJID)
        room.addUser(user)

        def userLeftRoom(room, user):
            self.assertEquals(self.roomIdentifier, room.roomIdentifier,
                              'Wrong room name')
            self.assertFalse(room.inRoster(user), 'User in roster')

        d, self.protocol.userLeftRoom = calledAsync(userLeftRoom)
        self.stub.send(parseXml(xml))
        return d


    def test_ban(self):
        """
        Ban an entity in a room.
        """
        banned = JID('ban@jabber.org/TroubleMaker')

        def cb(banned):
            self.assertTrue(banned, 'Did not ban user')

        d = self.protocol.ban(self.occupantJID, banned, reason='Spam',
                              sender=self.userJID)
        d.addCallback(cb)

        iq = self.stub.output[-1]

        self.assertTrue(xpath.matches(
                u"/iq[@type='set' and @to='%s']/query/item"
                    "[@affiliation='outcast']" % (self.roomJID,),
                iq),
            'Wrong ban stanza')

        response = toResponse(iq, 'result')
        self.stub.send(response)

        return d


    def test_kick(self):
        """
        Kick an entity from a room.
        """
        nick = 'TroubleMaker'

        def cb(kicked):
            self.assertTrue(kicked, 'Did not kick user')

        d = self.protocol.kick(self.occupantJID, nick, reason='Spam',
                               sender=self.userJID)
        d.addCallback(cb)

        iq = self.stub.output[-1]

        self.assertTrue(xpath.matches(
                u"/iq[@type='set' and @to='%s']/query/item"
                    "[@affiliation='none']" % (self.roomJID,),
                iq),
            'Wrong kick stanza')

        response = toResponse(iq, 'result')
        self.stub.send(response)

        return d


    def test_password(self):
        """
        Sending a password via presence to a password protected room.
        """

        self.protocol.password(self.occupantJID, 'secret')

        element = self.stub.output[-1]

        self.assertTrue(xpath.matches(
                u"/presence[@to='%s']/x/password"
                    "[text()='secret']" % (self.occupantJID,),
                element),
            'Wrong presence stanza')


    def test_receivedHistory(self):
        """
        Receiving history on room join.
        """
        xml = u"""
            <message to='test@test.com' from='%s' type='groupchat'>
              <body>test</body>
              <delay xmlns='urn:xmpp:delay' stamp="2002-10-13T23:58:37Z"
                                            from="%s"/>
            </message>
        """ % (self.occupantJID, self.userJID)

        self._createRoom()


        def receivedHistory(room, user, message):
            self.assertEquals('test', message.body, "wrong message body")
            stamp = datetime(2002, 10, 13, 23, 58, 37, tzinfo=tzutc())
            self.assertEquals(stamp, message.delay.stamp,
                             'Does not have a history stamp')

        d, self.protocol.receivedHistory = calledAsync(receivedHistory)
        self.stub.send(parseXml(xml))
        return d


    def test_oneToOneChat(self):
        """
        Converting a one to one chat to a multi-user chat.
        """
        archive = []
        thread = "e0ffe42b28561960c6b12b944a092794b9683a38"
        # create messages
        element = domish.Element((None, 'message'))
        element['to'] = 'testing@example.com'
        element['type'] = 'chat'
        element.addElement('body', None, 'test')
        element.addElement('thread', None, thread)

        archive.append({'stanza': element,
                        'timestamp': datetime(2002, 10, 13, 23, 58, 37,
                                              tzinfo=tzutc())})

        element = domish.Element((None, 'message'))
        element['to'] = 'testing2@example.com'
        element['type'] = 'chat'
        element.addElement('body', None, 'yo')
        element.addElement('thread', None, thread)

        archive.append({'stanza': element,
                        'timestamp': datetime(2002, 10, 13, 23, 58, 43,
                                              tzinfo=tzutc())})

        self.protocol.history(self.occupantJID, archive)


        while len(self.stub.output)>0:
            element = self.stub.output.pop()
            # check for delay element
            self.assertEquals('message', element.name, 'Wrong stanza')
            self.assertTrue(xpath.matches("/message/delay", element),
                            'Invalid history stanza')


    def test_invite(self):
        """
        Invite a user to a room
        """
        invitee = JID('other@example.org')

        self.protocol.invite(self.roomJID, invitee, u'This is a test')

        message = self.stub.output[-1]

        self.assertEquals('message', message.name)
        self.assertEquals(self.roomJID.full(), message.getAttribute('to'))
        self.assertEquals(muc.NS_MUC_USER, message.x.uri)
        self.assertEquals(muc.NS_MUC_USER, message.x.invite.uri)
        self.assertEquals(invitee.full(), message.x.invite.getAttribute('to'))
        self.assertEquals(muc.NS_MUC_USER, message.x.invite.reason.uri)
        self.assertEquals(u'This is a test', unicode(message.x.invite.reason))


    def test_groupChat(self):
        """
        Send private messages to muc entities.
        """
        self.protocol.groupChat(self.roomJID, u'This is a test')

        message = self.stub.output[-1]

        self.assertEquals('message', message.name)
        self.assertEquals(self.roomJID.full(), message.getAttribute('to'))
        self.assertEquals('groupchat', message.getAttribute('type'))
        self.assertEquals(u'This is a test', unicode(message.body))


    def test_chat(self):
        """
        Send private messages to muc entities.
        """
        otherOccupantJID = JID(self.occupantJID.userhost()+'/OtherNick')

        self.protocol.chat(otherOccupantJID, u'This is a test')

        message = self.stub.output[-1]

        self.assertEquals('message', message.name)
        self.assertEquals(otherOccupantJID.full(), message.getAttribute('to'))
        self.assertEquals('chat', message.getAttribute('type'))
        self.assertEquals(u'This is a test', unicode(message.body))


    def test_register(self):
        """
        Client registering with a room.

        http://xmpp.org/extensions/xep-0045.html#register
        """

        # FIXME: this doesn't really test the registration

        def cb(iq):
            # check for a result
            self.assertEquals('result', iq['type'], 'We did not get a result')

        d = self.protocol.register(self.roomJID)
        d.addCallback(cb)

        iq = self.stub.output[-1]
        query = "/iq/query[@xmlns='%s']" % muc.NS_REQUEST
        self.assertTrue(xpath.matches(query, iq), 'Invalid iq register request')

        response = toResponse(iq, 'result')
        self.stub.send(response)
        return d


    def test_voice(self):
        """
        Client requesting voice for a room.
        """
        self.protocol.voice(self.occupantJID)

        m = self.stub.output[-1]

        query = ("/message/x[@type='submit']/field/value"
                    "[text()='%s']") % muc.NS_MUC_REQUEST
        self.assertTrue(xpath.matches(query, m), 'Invalid voice message stanza')


    def test_roomConfigure(self):
        """
        Default configure and changing the room name.
        """

        def cb(iq):
            self.assertEquals('result', iq['type'], 'Not a result')


        fields = []

        fields.append(data_form.Field(label='Natural-Language Room Name',
                                      var='muc#roomconfig_roomname',
                                      value=self.roomIdentifier))

        d = self.protocol.configure(self.roomJID, fields)
        d.addCallback(cb)

        iq = self.stub.output[-1]
        query = "/iq/query[@xmlns='%s']/x"% muc.NS_MUC_OWNER
        self.assertTrue(xpath.matches(query, iq), 'Bad configure request')

        response = toResponse(iq, 'result')
        self.stub.send(response)
        return d


    def test_destroy(self):
        """
        Destroy a room.
        """
        d = self.protocol.destroy(self.occupantJID, reason='Time to leave',
                                  alternate=JID('other@%s' % self.service),
                                  password='secret')

        iq = self.stub.output[-1]

        query = ("/iq/query[@xmlns='%s']/destroy[@xmlns='%s']" %
                 (muc.NS_MUC_OWNER, muc.NS_MUC_OWNER))

        nodes = xpath.queryForNodes(query, iq)
        self.assertNotIdentical(None, nodes, 'Bad configure request')
        destroy = nodes[0]
        self.assertEquals('Time to leave', unicode(destroy.reason))

        response = toResponse(iq, 'result')
        self.stub.send(response)
        return d


    def test_subject(self):
        """
        Change subject of the room.
        """
        self.protocol.subject(self.roomJID, u'This is a test')

        message = self.stub.output[-1]

        self.assertEquals('message', message.name)
        self.assertEquals(self.roomJID.full(), message.getAttribute('to'))
        self.assertEquals('groupchat', message.getAttribute('type'))
        self.assertEquals(u'This is a test', unicode(message.subject))


    def test_nick(self):
        """
        Send a nick change to the server.
        """
        newNick = 'newNick'

        self._createRoom()

        def cb(room):
            self.assertEquals(self.roomIdentifier, room.roomIdentifier)
            self.assertEquals(newNick, room.nick)

        d = self.protocol.nick(self.roomJID, newNick)
        d.addCallback(cb)

        element = self.stub.output[-1]
        self.assertEquals('presence', element.name, "Need to be presence")
        self.assertNotIdentical(None, element.x, 'No muc x element')

        # send back user presence, nick changed
        xml = u"""
            <presence from='%s/%s'>
              <x xmlns='http://jabber.org/protocol/muc#user'>
                <item affiliation='member' role='participant'/>
              </x>
            </presence>
        """ % (self.roomJID, newNick)
        self.stub.send(parseXml(xml))
        return d


    def test_nickConflict(self):
        """
        If the server finds the new nick in conflict, the errback is called.
        """
        newNick = 'newNick'

        self._createRoom()

        d = self.protocol.nick(self.roomJID, newNick)
        self.assertFailure(d, StanzaError)

        element = self.stub.output[-1]
        self.assertEquals('presence', element.name, "Need to be presence")
        self.assertNotIdentical(None, element.x, 'No muc x element')

        # send back user presence, nick changed
        xml = u"""
            <presence from='%s/%s' type='error'>
                <x xmlns='http://jabber.org/protocol/muc'/>
                <error type='cancel'>
                  <conflict xmlns='urn:ietf:params:xml:ns:xmpp-stanzas'/>
                </error>
            </presence>
        """ % (self.roomJID, newNick)
        self.stub.send(parseXml(xml))
        return d


    def test_grantVoice(self):
        """
        Test granting voice to a user.

        """
        nick = 'TroubleMaker'
        def cb(give_voice):
            self.assertTrue(give_voice, 'Did not give voice user')

        d = self.protocol.grantVoice(self.occupantJID, nick,
                                     sender=self.userJID)
        d.addCallback(cb)

        iq = self.stub.output[-1]

        query = (u"/iq[@type='set' and @to='%s']/query/item"
                     "[@role='participant']") % self.roomJID
        self.assertTrue(xpath.matches(query, iq), 'Wrong voice stanza')

        response = toResponse(iq, 'result')
        self.stub.send(response)
        return d


    def test_status(self):
        """
        Change status
        """
        self._createRoom()
        room = self.protocol._getRoom(self.roomJID)
        user = muc.User(self.nick)
        room.addUser(user)

        def cb(room):
            self.assertEquals(self.roomIdentifier, room.roomIdentifier)
            user = room.getUser(self.nick)
            self.assertNotIdentical(None, user, 'User not found')
            self.assertEquals('testing MUC', user.status, 'Wrong status')
            self.assertEquals('xa', user.show, 'Wrong show')

        d = self.protocol.status(self.roomJID, 'xa', 'testing MUC')
        d.addCallback(cb)

        element = self.stub.output[-1]

        self.assertEquals('presence', element.name, "Need to be presence")
        self.assertTrue(getattr(element, 'x', None), 'No muc x element')

        # send back user presence, status changed
        xml = u"""
            <presence from='%s'>
              <x xmlns='http://jabber.org/protocol/muc#user'>
                <item affiliation='member' role='participant'/>
              </x>
              <show>xa</show>
              <status>testing MUC</status>
            </presence>
        """ % self.occupantJID
        self.stub.send(parseXml(xml))
        return d


    def test_getMemberList(self):
        def cb(room):
            members = room.members
            self.assertEquals(1, len(members))
            user = members[0]
            self.assertEquals(JID(u'hag66@shakespeare.lit'), user.entity)
            self.assertEquals(u'thirdwitch', user.nick)
            self.assertEquals(u'participant', user.role)

        self._createRoom()
        d = self.protocol.getMemberList(self.roomJID)
        d.addCallback(cb)

        iq = self.stub.output[-1]
        query = iq.query
        self.assertNotIdentical(None, query)
        self.assertEquals(NS_MUC_ADMIN, query.uri)

        response = toResponse(iq, 'result')
        query = response.addElement((NS_MUC_ADMIN, 'query'))
        item = query.addElement('item')
        item['affiliation'] ='member'
        item['jid'] = 'hag66@shakespeare.lit'
        item['nick'] = 'thirdwitch'
        item['role'] = 'participant'
        self.stub.send(response)

        return d