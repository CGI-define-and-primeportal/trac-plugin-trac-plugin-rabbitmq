from celery import Celery
import yaml
import urllib
import pprint
import pkg_resources

# ./development-environment/bin/pip install python-qpid-proton
from proton import Messenger, Message

from trac.core import Component, implements
from trac.config import Option
from trac.resource import Resource, get_resource_url
from trac.admin import IAdminCommandProvider, IAdminPanelProvider
from trac.web.chrome import ITemplateProvider, add_notice

from api import ICeleryTask

# TODO, read the backend and broker from trac.ini
app = Celery('msservicebus', backend='amqp', broker='amqp://')

@app.task(name='post-to-msservicebus')
def relay_event(issuer, key, namespace, queuename, event):
    # TODO can do this faster by persisting something? Maybe the Messenger? How to do that with celery threading?
    messenger = Messenger()
    message = Message()
    message.address = "amqps://{issuer}:{key}@{namespace}.servicebus.windows.net/{queuename}".format(
        issuer = issuer,
        key = urllib.quote(key, ""),
        namespace = namespace,
        queuename = queuename)

    message.properties = {}
    # TODO align with Service Bus / Service Tool team
    message.properties[u"DefineProject"] = event['project']
    del event['project']
    message.properties[u"EventCategory"] = event['category']
    del event['category']
    if 'ticket' in event:
        message.properties[u"Ticket"] = event['ticket']
        del event['ticket']
        message.properties[u"Actor"] = event['author']
        del event['author']

    message.body = event
    messenger.put(message)
    messenger.send()

class MSServiceBusEmitter(Component):
    implements(ICeleryTask, IAdminPanelProvider, ITemplateProvider, IAdminCommandProvider)

    issuer = Option('microsoft servicebus', 'issuer')
    key = Option('microsoft servicebus', 'key')
    namespace = Option('microsoft servicebus', 'namespace')
    queuename = Option('microsoft servicebus', 'queuename')
    adminpagename = Option('microsoft servicebus', 'admin_page_name', 'Azure Service Bus')

    # IAdminPanelProvider
    def get_admin_panels(self, req):
        if req.perm.has_permission('TRAC_ADMIN'):
            yield ('integrations', 'Integrations', 'servicebus', self.adminpagename)

    def render_admin_panel(self, req, cat, page, path_info):
        if req.method == 'POST':
            self.config.set('microsoft servicebus', 'issuer', req.args.get('issuer'))
            self.config.set('microsoft servicebus', 'key', req.args.get('key'))
            self.config.set('microsoft servicebus', 'namespace', req.args.get('namespace'))
            self.config.set('microsoft servicebus', 'queuename', req.args.get('queuename'))
            self.config.save()
            add_notice(req, "Saved integration settings")
            req.redirect(req.href.admin(cat, page))
                
        return 'servicebus_admin.html', {'issuer': self.issuer,
                                         'key': self.key,
                                         'namespace': self.namespace,
                                         'queuename': self.queuename}

    # ITemplateProvider
    def get_htdocs_dirs(self):
        yield 'servicebus', pkg_resources.resource_filename(__name__,
                                                            'htdocs')

    def get_templates_dirs(self):
        yield pkg_resources.resource_filename(__name__, 'templates')


    # ICeleryTask
    def run(self, event):
        if self.issuer and self.key and self.namespace and self.queuename:
            return relay_event.delay(self.issuer,
                                     self.key,
                                     self.namespace,
                                     self.queuename,
                                     event)

    # IAdminCommandProvider
    def get_admin_commands(self):
        yield ('servicebus dump', '[verbose]',
               "Consume and print all messages from the Service Bus queue",
               None, self._do_dump)

    def _do_dump(self, args=None):

        if not (self.issuer and self.key and self.namespace and self.queuename):
            print "Service bus is not configured"
            return
            
        messenger = Messenger()

        address_syntax = "amqps://{issuer}:{key}@{namespace}.servicebus.windows.net/{queuename}"
        address = address_syntax.format(issuer = self.issuer,
                                        key = urllib.quote(self.key, ""),
                                        namespace = self.namespace,
                                        queuename = self.queuename)
        censored_address = address_syntax.format(issuer = self.issuer,
                                                 key = "XXX",
                                                 namespace = self.namespace,
                                                 queuename = self.queuename)

        print "Subscribing to %s" % censored_address
        messenger.subscribe(address)

        # https://github.com/Azure/azure-service-bus-samples/blob/master/proton-c-queues-and-topics/receiver.c
        messenger.incoming_window = 10

        messenger.start()
        while True:
            messenger.recv()
            while messenger.incoming:
                message = Message()
                tracker = messenger.get(message)
                if args == "verbose":
                    print "-" * 32
                    pprint.pprint(message.address)
                    pprint.pprint(message.delivery_count)
                    pprint.pprint(message.annotations)
                    pprint.pprint(message.properties)
                    pprint.pprint(message.body)
                else:
                    print message.delivery_count, message.properties
                messenger.accept(tracker)
        messenger.stop()
