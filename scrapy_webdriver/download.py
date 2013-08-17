import signal

from scrapy import log
from scrapy.utils.decorator import inthread
from scrapy.utils.misc import load_object
from .manager import WebdriverManager

from .http import WebdriverActionRequest, WebdriverRequest, WebdriverResponse

FALLBACK_HANDLER = 'scrapy.core.downloader.handlers.http.HttpDownloadHandler'

class WebdriverTimeout(Exception):
    pass

class WebdriverDownloadHandler(object):
    """This download handler uses webdriver, deferred in a thread.

    Falls back to the stock scrapy download handler for non-webdriver requests.

    """
    def __init__(self, settings):
        self._enabled = settings.get('WEBDRIVER_BROWSER') is not None
        self._timeout = settings.get('WEBDRIVER_TIMEOUT')
        self._hang_timeout = settings.get('WEBDRIVER_HANG_TIMEOUT', None)
        self._fallback_handler = load_object(FALLBACK_HANDLER)(settings)
        self._manager = WebdriverManager(settings)

    def close(self):
        self._manager.cleanup()

    def download_request(self, request, spider):
        """Return the result of the right download method for the request."""
        if self._enabled and isinstance(request, WebdriverRequest):

            # set the signal handler for the SIGALRM event
            if self._hang_timeout:

                def alarm_handler(signum, frame):

                    # kill the selenium webdriver process (with SIGTERM,
                    # so that it kills both the primary process and the
                    # process that gets spawned)
                    self._manager.webdriver.service.process.send_signal(signal.SIGTERM)

                    # set the defunct _webdriver attribute back to
                    # original value of None, so that the next time it is
                    # accessed it is recreated.
                    self._manager._webdriver = None

                    self._manager.release()

                    # log an informative warning message
                    msg = "WebDriver.get for '%s' took more than WEBDRIVER_HANG_TIMEOUT (%ss)" % \
                        (request.url, self._hang_timeout)
                    spider.log(msg, level=log.INFO)

                # bind the handler
                signal.signal(signal.SIGALRM, alarm_handler)

            if isinstance(request, WebdriverActionRequest):
                raise NotImplementedError()
                #download = self._do_action_request
            else:
                download = self._download_request
        else:
            download = self._fallback_handler.download_request
        return download(request, spider)

    @inthread
    def _download_request(self, request, spider):
        """Download a request URL using webdriver."""
        spider.log('Downloading %s with webdriver' % request.url, level=log.DEBUG)

        # set a countdown timer for the webdriver.get
        if self._hang_timeout:
            signal.alarm(self._hang_timeout)

        # make the get request
        try:
            self._manager.acquire(request)
            self._manager.webdriver.get(request.url)

        # if the get fails for any reason, set the webdriver attribute of the
        # response to the exception that occurred
        except Exception, exception:

            # since it's already failed, don't try to raise alarm anymore (this has no effect if the failure was due to the alarm)
            if self._hang_timeout:
                spider.log('settings alarm to 0 on FAILURE', level=log.DEBUG)
                spider.log('FAIL: ' + str(self._manager._webdriver), level=log.DEBUG)
                signal.alarm(0)

            # set page_source to blank so that WebdriverResponse doesn't complain
            exception.page_source = '<html><head></head><body></body></html>'

            # log a nice error message
            msg = 'Error while downloading %s with webdriver (%s)' % \
                (request.url, exception)
            spider.log(msg, level=log.ERROR)

            # since manager.webdriver is a @property, this will recreate connection
            self._manager.webdriver
            spider.log('FAIL 2. THIS SHOULD BE WEBDRIVER: ' + str(self._manager._webdriver), level=log.DEBUG)
            return WebdriverResponse(request.url, exception)

        # if the get finishes, defuse the bomb and return a response with the
        # webdriver attached
        else:

            # since it succeeded, don't raise any alarm
            if self._hang_timeout:
                spider.log('settings alarm to 0 on SUCCESS', level=log.DEBUG)
                spider.log('YEAH: ' + str(self._manager._webdriver), level=log.DEBUG)
                signal.alarm(0)

            # return the correct response
            return WebdriverResponse(request.url, self._manager.webdriver)
        finally:
            self._manager.release()
            
    @inthread
    def _do_action_request(self, request, spider):
        """Perform an action on a previously webdriver-loaded page."""
        log.msg('Running webdriver actions %s' % request.url, level=log.DEBUG)
        request.actions.perform()
        return WebdriverResponse(request.url, self._manager.webdriver)
