from scrapy.exceptions import IgnoreRequest, NotConfigured
from scrapy import log

from .http import WebdriverActionRequest, WebdriverRequest, WebdriverResponse
from .manager import WebdriverManager

class WebdriverSpiderMiddleware(object):
    """This middleware coordinates concurrent webdriver access attempts."""
    def __init__(self, crawler):
        self.manager = WebdriverManager(crawler)

    @classmethod
    def from_crawler(cls, crawler):
        try:
            return cls(crawler)
        except Exception as e:
            raise NotConfigured('WEBDRIVER_BROWSER is misconfigured: %r (%r)'
                % (crawler.settings.get('WEBDRIVER_BROWSER'), e))

    def process_start_requests(self, start_requests, spider):
        """Return start requests, with some reordered by the manager.

        The reordering occurs as a result of some requests waiting to gain
        access to the webdriver instance. Those waiting requests are queued up
        in the manager, from which we pop the next in line after we release the
        webdriver instance while processing spider output.

        """
        return self._process_requests(start_requests, start=True)

    def process_spider_output(self, response, result, spider):
        """Return spider result, with some requests reordered by the manager.

        See ``process_start_requests`` for a description of the reordering.

        """
        for item_or_request in self._process_requests(result):
            yield item_or_request
        if isinstance(response.request, WebdriverRequest):
            # We are here because the current request holds the webdriver lock.
            # That lock was kept for the entire duration of the response
            # parsing callback to keep the webdriver instance intact, and we
            # now release it.
            self.manager.release(response.request.url)
            next_request = self.manager.acquire_next()
            if next_request is not WebdriverRequest.WAITING:
                yield next_request.replace(dont_filter=True)

    def _process_requests(self, items_or_requests, start=False):
        """Acquire the webdriver manager when it's available for requests."""
        error_msg = "WebdriverRequests from start_requests can't be in-page."
        for request in iter(items_or_requests):
            if isinstance(request, WebdriverRequest):
                if start and isinstance(request, WebdriverActionRequest):
                    raise IgnoreRequest(error_msg)
                request = self.manager.acquire(request)
                if request is WebdriverRequest.WAITING:
                    continue  # Request has been enqueued, so drop it.
            yield request

    def process_spider_exception(self, response, exception, spider):
        """If there is an exception while parsing, feed the scrapy
        scheduler with the next request from the queue in the
        webdriver manager.
        """
        if isinstance(response.request, WebdriverRequest):

            # release the lock that was acquired for this URL
            self.manager.release(response.request.url)

            next_request = self.manager.acquire_next()
            return [next_request]

class WebdriverDownloaderMiddleware(object):
    """This middleware handles webdriver.get failures."""

    def process_response(self, request, response, spider):

        # if there is a downloading error in the WebdriverResponse,
        # make a nice error message
        if isinstance(response, WebdriverResponse):
            if response.exception:
                msg = 'Error while downloading %s with webdriver (%s)' % \
                    (request.url, response.exception)
                spider.log(msg, level=log.ERROR)

        # but always still return the response. When there are errors,
        # parse methods will probably fail.
        return response

