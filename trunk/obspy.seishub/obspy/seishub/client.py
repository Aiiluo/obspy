# -*- coding: utf-8 -*-
"""
SeisHub database client for ObsPy.

:copyright:
    The ObsPy Development Team (devs@obspy.org)
:license:
    GNU Lesser General Public License, Version 3
    (http://www.gnu.org/copyleft/lesser.html)
"""

from datetime import datetime
from lxml import objectify
from lxml.etree import Element, SubElement, tostring
from math import log
from obspy.core import UTCDateTime
from obspy.core.util import BAND_CODE, deprecated, deprecated_keywords
import os
import pickle
import sys
import time
import urllib
import urllib2
import warnings


HTTP_ACCEPTED_DATA_METHODS = ["PUT", "POST"]
HTTP_ACCEPTED_NODATA_METHODS = ["HEAD", "GET", "DELETE"]
HTTP_ACCEPTED_METHODS = HTTP_ACCEPTED_DATA_METHODS + \
                        HTTP_ACCEPTED_NODATA_METHODS


DEPRECATED_KEYWORDS = {'network_id':'network', 'station_id':'station',
                       'location_id':'location', 'channel_id':'channel',
                       'start_datetime':'starttime', 'end_datetime':'endtime'}
KEYWORDS = {'network':'network_id', 'station':'station_id',
            'location':'location_id', 'channel':'channel_id',
            'starttime':'start_datetime', 'endtime':'end_datetime'}


class Client(object):
    """
    SeisHub database request Client class.

    Notes
    -----
    The following classes are automatically linked with initialization.
    Follow the links in "Linked Class" for more information. They register
    via the name listed in "Entry Point".

    ===================  ====================================================
    Entry Point          Linked Class
    ===================  ====================================================
    ``Client.waveform``  :class:`~obspy.seishub.client._WaveformMapperClient`
    ``Client.station``   :class:`~obspy.seishub.client._StationMapperClient`
    ``Client.event``     :class:`~obspy.seishub.client._EventMapperClient`
    ===================  ====================================================

    Examples
    --------

    >>> from obspy.seishub import Client
    >>> from obspy.core import UTCDateTime
    >>>
    >>> t = UTCDateTime("2009-09-03 00:00:00")
    >>> client = Client()
    >>>
    >>> st = client.waveform.getWaveform("BW", "RTPI", "", "EHZ", t, t + 20)
    >>> print(st)
    1 Trace(s) in Stream:
    .GP01..SHZ | 2009-09-03T00:00:00.000000Z - 2009-09-03T00:00:20.000000Z | 250.0 Hz, 5001 samples
    """
    def __init__(self, base_url="http://teide.geophysik.uni-muenchen.de:8080",
                 user="admin", password="admin", timeout=10):
        self.base_url = base_url
        self.waveform = _WaveformMapperClient(self)
        self.station = _StationMapperClient(self)
        self.event = _EventMapperClient(self)
        self.timeout = timeout
        # Create an OpenerDirector for Basic HTTP Authentication
        password_mgr = urllib2.HTTPPasswordMgrWithDefaultRealm()
        password_mgr.add_password(None, base_url, user, password)
        auth_handler = urllib2.HTTPBasicAuthHandler(password_mgr)
        opener = urllib2.build_opener(auth_handler)
        # install globally
        urllib2.install_opener(opener)

    def ping(self):
        """
        Ping the SeisHub server.
        """
        try:
            t1 = time.time()
            urllib2.urlopen(self.base_url).read()
            return (time.time() - t1) * 1000.0
        except:
            None

    def testAuth(self):
        """
        Test if authentication information is valid. Raises an Exception if
        status code of response is not 200 (OK) or 401 (Forbidden).

        Returns
        -------
            True if OK, False if invalid.
        """
        (code, _msg) = self._HTTP_request(self.base_url + "/xml/",
                                         method="HEAD")
        if code == 200:
            return True
        elif code == 401:
            return False
        else:
            raise Exception("Unexpected request status code: %s" % code)

    def _fetch(self, url, *args, **kwargs):
        params = {}
        # map keywords
        for key, value in KEYWORDS.iteritems():
            if key in kwargs.keys():
                kwargs[value] = kwargs[key]
                del kwargs[key]
        # check for ranges and empty values
        for key, value in kwargs.iteritems():
            if not value:
                continue
            if isinstance(value, tuple) and len(value) == 2:
                params['min_' + str(key)] = str(value[0])
                params['max_' + str(key)] = str(value[1])
            elif isinstance(value, list) and len(value) == 2:
                params['min_' + str(key)] = str(value[0])
                params['max_' + str(key)] = str(value[1])
            else:
                params[str(key)] = str(value)
        # replace special characters 
        remoteaddr = self.base_url + url + '?' + urllib.urlencode(params)
        # timeout exists only for Python >= 2.6
        if sys.hexversion < 0x02060000:
            response = urllib2.urlopen(remoteaddr)
        else:
            response = urllib2.urlopen(remoteaddr, timeout=self.timeout)
        doc = response.read()

        return doc

    def _HTTP_request(self, url, method, xml_string="", headers={}):
        """
        Send a HTTP request via urllib2.

        :type url: String
        :param url: Complete URL of resource
        :type method: String
        :param method: HTTP method of request, e.g. "PUT"
        :type headers: dict
        :param headers: Header information for request, e.g.
                {'User-Agent': "obspyck"}
        :type xml_string: String
        :param xml_string: XML for a send request (PUT/POST)
        """
        if method not in HTTP_ACCEPTED_METHODS:
            raise ValueError("Method must be one of %s" % \
                             HTTP_ACCEPTED_METHODS)
        if method in HTTP_ACCEPTED_DATA_METHODS and not xml_string:
            raise TypeError("Missing data for %s request." % method)
        elif method in HTTP_ACCEPTED_NODATA_METHODS and xml_string:
            raise TypeError("Unexpected data for %s request." % method)

        req = RequestWithMethod(method=method, url=url, data=xml_string,
                                headers=headers)
        # it seems the following always ends in a urllib2.HTTPError even with
        # nice status codes...?!?
        try:
            response = urllib2.urlopen(req)
            return response.code, response.msg
        except urllib2.HTTPError, e:
            return e.code, e.msg

    def _objectify(self, url, *args, **kwargs):
        doc = self._fetch(url, *args, **kwargs)
        return objectify.fromstring(doc)


class _BaseRESTClient(object):
    def __init__(self, client):
        self.client = client

    def getResource(self, resource_name, format=None, **kwargs):
        """
        Gets a resource.

        Parameters
        ----------
        resource_name : string
            Name of the resource.
        format : string, optional
            Format string, e.g. 'xml' or 'map'.

        Returns
        -------
            Resource
        """
        # NOTHING goes ABOVE this line!
        for key, value in locals().iteritems():
            if key not in ["self", "kwargs"]:
                kwargs[key] = value
        url = '/xml/' + self.package + '/' + self.resourcetype + '/' + \
              resource_name
        return self.client._fetch(url, **kwargs)

    def getXMLResource(self, resource_name, **kwargs):
        """
        Gets a XML resource.

        Parameters
        ----------
        resource_name : string
            Name of the resource.

        Returns
        -------
            Resource as :class:`lxml.objectify.ObjectifiedElement`
        """
        url = '/xml/' + self.package + '/' + self.resourcetype + '/' + \
              resource_name
        return self.client._objectify(url, **kwargs)

    def putResource(self, resource_name, xml_string, headers={}):
        """
        PUTs a XML resource.

        Parameters
        ----------
        resource_name : string
            Name of the resource.
        headers : dict
            Header information for request, e.g. {'User-Agent': "obspyck"}
        xml_string : string
            XML for a send request (PUT/POST)

        Returns
        -------
            (HTTP status code, HTTP status message)
        """
        url = '/'.join([self.client.base_url, 'xml', self.package,
                        self.resourcetype, resource_name])
        return self.client._HTTP_request(url, method="PUT",
                xml_string=xml_string, headers=headers)

    def deleteResource(self, resource_name, headers={}):
        """
        DELETEs a XML resource.

        Parameters
        ----------
        resource_name : string
            Name of the resource.
        headers : dict
            Header information for request, e.g. {'User-Agent': "obspyck"}

        Returns
        -------
            (HTTP status code, HTTP status message)
        """
        url = '/'.join([self.client.base_url, 'xml', self.package,
                        self.resourcetype, resource_name])
        return self.client._HTTP_request(url, method="DELETE",
                headers=headers)


class _WaveformMapperClient(object):
    """
    Waveform class to access the SeisHub waveform-mapper_

    .. _waveform-mapper: http://svn.geophysik.uni-muenchen.de/trac/seishub/browser/trunk/plugins/seishub.plugins.seismology/seishub/plugins/seismology/waveform.py
    """
    def __init__(self, client):
        self.client = client

    def getNetworkIds(self, **kwargs):
        """
        Gets a list of network ids.

        Returns
        -------
            List of containing network ids.
        """
        url = '/seismology/waveform/getNetworkIds'
        root = self.client._objectify(url, **kwargs)
        return [str(node['network']) for node in root.getchildren()]

    @deprecated_keywords({'network_id':'network'})
    def getStationIds(self, network=None, **kwargs):
        """
        Gets a list of station ids.

        Parameters
        ----------
        network : string
            Network code, e.g. 'BW'. If not specified, station ids of all
            networks are returned.

        Returns
        -------
            List of containing station ids.
        """
        # NOTHING goes ABOVE this line!
        for key, value in locals().iteritems():
            if key not in ["self", "kwargs"]:
                kwargs[key] = value
        url = '/seismology/waveform/getStationIds'
        root = self.client._objectify(url, **kwargs)
        return [str(node['station']) for node in root.getchildren()]

    @deprecated_keywords({'network_id':'network', 'station_id':'station'})
    def getLocationIds(self, network=None, station=None, **kwargs):
        """
        Gets a list of location ids.

        Parameters
        ----------
        network : string
            Network code, e.g. 'BW'.
        station : string
            Station code, e.g. 'MANZ'.

        Returns
        -------
            List of containing location ids.
        """
        # NOTHING goes ABOVE this line!
        for key, value in locals().iteritems():
            if key not in ["self", "kwargs"]:
                kwargs[key] = value
        url = '/seismology/waveform/getLocationIds'
        root = self.client._objectify(url, **kwargs)
        return [str(node['location']) for node in root.getchildren()]

    @deprecated_keywords({'network_id':'network', 'station_id':'station',
                          'location_id':'location'})
    def getChannelIds(self, network=None, station=None, location=None,
                      **kwargs):
        """
        Gets a list of channel ids.

        Parameters
        ----------
        network : string
            Network code, e.g. 'BW'.
        station : string
            Station code, e.g. 'MANZ'.
        location : string
            Location code, e.g. '00'.

        Returns
        -------
            List of containing channel ids.
        """
        # NOTHING goes ABOVE this line!
        for key, value in locals().iteritems():
            if key not in ["self", "kwargs"]:
                kwargs[key] = value
        url = '/seismology/waveform/getChannelIds'
        root = self.client._objectify(url, **kwargs)
        return [str(node['channel']) for node in root.getchildren()]

    @deprecated_keywords({'network_id':'network', 'station_id':'station',
                          'location_id':'location', 'channel_id':'channel'})
    def getLatency(self, network=None, station=None, location=None,
                   channel=None, **kwargs):
        """
        Gets a list of network latency values.

        Parameters
        ----------
        network : string
            Network code, e.g. 'BW'.
        station : string
            Station code, e.g. 'MANZ'.
        location : string
            Location code, e.g. '00'.
        channel : string
            Channel code, e.g. 'EHE'.

        Returns
        -------
            List of dictionaries containing latency information.
        """
        # NOTHING goes ABOVE this line!
        for key, value in locals().iteritems():
            if key not in ["self", "kwargs"]:
                kwargs[key] = value
        url = '/seismology/waveform/getLatency'
        root = self.client._objectify(url, **kwargs)
        return [dict(((k, v.pyval) for k, v in node.__dict__.iteritems())) \
                for node in root.getchildren()]

    @deprecated_keywords(DEPRECATED_KEYWORDS)
    def getWaveform(self, network, station, location=None, channel=None,
                    starttime=None, endtime=None, apply_filter=False,
                    getPAZ=False, getCoordinates=False,
                    metadata_timecheck=True, **kwargs):
        """
        Gets a ObsPy Stream object.

        Parameters
        ----------
        network : string
            Network code, e.g. 'BW'.
        station : string
            Station code, e.g. 'MANZ'.
        location : string
            Location code, e.g. '00'.
        channel : string
            Channel code, supporting wildcard for component, e.g. 'EHE' or 
            'EH*'.
        starttime : :class:`~obspy.core.utcdatetime.UTCDateTime`
            Start date and time.
        endtime : :class:`~obspy.core.utcdatetime.UTCDateTime`
            End date and time.
        apply_filter : boolean, optional
            Apply filter (default is False).
        getPAZ : boolean, optional
            Fetch PAZ information and append to 
            :class:`~obspy.core.trace.Stats` of all fetched traces. This
            considerably slows down the request (default is False).
        getCoordinates : boolean, optional
            Fetch coordinate information and append to
            :class:`~obspy.core.trace.Stats` of all fetched traces. This
            considerably slows down the request (default is False).
        metadata_timecheck : boolean, optional
            For ``getPAZ`` and ``getCoordinates`` check if metadata information
            is changing from start to end time. Raises an Exception if this is
            the case. This can be deactivated to save time.

        Returns
        -------
            :class:`~obspy.core.stream.Stream`
        """
        # NOTHING goes ABOVE this line!
        # append all args to kwargs, thus having everything in one dictionary
        for key, value in locals().iteritems():
            if key not in ["self", "kwargs"]:
                kwargs[key] = value

        # allow time strings in arguments
        for time in ["starttime", "endtime"]:
            if isinstance(kwargs[time], str):
                kwargs[time] = UTCDateTime(kwargs[time])
        # we expand the requested timespan on both ends by two samples in
        # order to be able to make use of the nearest_sample option of
        # stream.trim(). (see trim() and tickets #95 and #105)
        # only possible if a channel is specified.
        if channel:
            band_code = kwargs['channel'][0]
            trim_start = kwargs['starttime']
            trim_end = kwargs['endtime']
            kwargs['starttime'] = trim_start - 2.0 / BAND_CODE[band_code]
            kwargs['endtime'] = trim_end + 2.0 / BAND_CODE[band_code]
        else:
            msg = "No channel id provided. Specifying a channel id can " + \
                  "lead to better selection of first/last samples of " + \
                  "fetched traces."
            warnings.warn(msg)

        url = '/seismology/waveform/getWaveform'
        data = self.client._fetch(url, **kwargs)
        if data == '':
            raise Exception("No waveform data available")
        # unpickle
        stream = pickle.loads(data)
        if len(stream) == 0:
            raise Exception("No waveform data available")

        # trimming needs to be done only if we extend the datetime above
        if channel:
            stream.trim(trim_start, trim_end)
        if getPAZ:
            paz = self.client.station.getPAZ(network=network, station=station,
                            location=location, channel=channel,
                            datetime=starttime)
            if metadata_timecheck:
                paz_check = self.client.station.getPAZ(network=network,
                        station=station, location=location, channel=channel,
                        datetime=endtime)
                if paz != paz_check:
                    msg = "PAZ information changing from start time to " + \
                          "end time."
                    raise Exception(msg)
            for tr in stream:
                tr.stats['paz'] = paz.copy()
        if getCoordinates:
            coords = self.client.station.getCoordinates(network=network,
                    station=station, location=location,
                    datetime=starttime)
            if metadata_timecheck:
                coords_check = self.client.station.getCoordinates(
                        network=network, station=station,
                        location=location, datetime=endtime)
                if coords != coords_check:
                    msg = "Coordinate information changing from start " + \
                          "time to end time."
                    raise Exception(msg)
            for tr in stream:
                tr.stats['coordinates'] = coords.copy()
        return stream

    @deprecated_keywords(DEPRECATED_KEYWORDS)
    def getPreview(self, network, station, location=None, channel=None,
                   starttime=None, endtime=None, trace_ids=None, **kwargs):
        """
        Gets a preview of a ObsPy Stream object.

        Parameters
        ----------
        network : string
            Network code, e.g. 'BW'.
        station : string
            Station code, e.g. 'MANZ'.
        location : string
            Location code, e.g. '00'.
        channel : string
            Channel code, supporting wildcard for component, e.g. 'EHE' or 
            'EH*'.
        starttime : :class:`~obspy.core.utcdatetime.UTCDateTime`
            Start date and time.
        endtime : :class:`~obspy.core.utcdatetime.UTCDateTime`
            End date and time.

        Returns
        -------
            :class:`~obspy.core.stream.Stream`
        """
        # NOTHING goes ABOVE this line!
        for key, value in locals().iteritems():
            if key not in ["self", "kwargs"]:
                kwargs[key] = value

        url = '/seismology/waveform/getPreview'
        data = self.client._fetch(url, **kwargs)
        if not data:
            raise Exception("No waveform data available")
        # unpickle
        stream = pickle.loads(data)
        return stream

    @deprecated_keywords({'start_datetime':'starttime',
                          'end_datetime':'endtime'})
    def getPreviewByIds(self, trace_ids=None, starttime=None, endtime=None,
                        **kwargs):
        """
        Gets a preview of a ObsPy Stream object.

        trace_ids : list
            List of trace IDs, e.g. ['BW.MANZ..EHE'].
        starttime : :class:`~obspy.core.utcdatetime.UTCDateTime`
            Start date and time.
        endtime : :class:`~obspy.core.utcdatetime.UTCDateTime`
            End date and time.

        Returns
        -------
            :class:`~obspy.core.stream.Stream`
        """
        # NOTHING goes ABOVE this line!
        for key, value in locals().iteritems():
            if key not in ["self", "kwargs"]:
                kwargs[key] = value
        # concatenate list of IDs into string
        if 'trace_ids' in kwargs:
            if isinstance(kwargs['trace_ids'], list):
                kwargs['trace_ids'] = ','.join(kwargs['trace_ids'])
        url = '/seismology/waveform/getPreview'
        data = self.client._fetch(url, **kwargs)
        if not data:
            raise Exception("No waveform data available")
        # unpickle
        stream = pickle.loads(data)
        return stream


class _StationMapperClient(_BaseRESTClient):
    """
    Station class to access the SeisHub station-mapper_

    .. _station-mapper: http://svn.geophysik.uni-muenchen.de/trac/seishub/browser/trunk/plugins/seishub.plugins.seismology/seishub/plugins/seismology/station.py
    """
    package = 'seismology'
    resourcetype = 'station'

    @deprecated_keywords({'network_id':'network', 'station_id':'station'})
    def getList(self, network=None, station=None, **kwargs):
        """
        Gets a list of station information.

        Parameters
        ----------
        network : string
            Network code, e.g. 'BW'.
        station : string
            Station code, e.g. 'MANZ'.

        Returns
        -------
            List of dictionaries containing station information.
        """
        # NOTHING goes ABOVE this line!
        for key, value in locals().iteritems():
            if key not in ["self", "kwargs"]:
                kwargs[key] = value
        url = '/seismology/station/getList'
        root = self.client._objectify(url, **kwargs)
        return [dict(((k, v.pyval) for k, v in node.__dict__.iteritems())) \
                for node in root.getchildren()]

    @deprecated_keywords({'network_id':'network', 'station_id':'station',
                          'location_id':'location'})
    def getCoordinates(self, network, station, datetime, location=''):
        """
        Get coordinate information.

        Returns a dictionary with coordinate information for specified station
        at the specified time.

        Parameters
        ----------
        network : string
            Network code, e.g. 'BW'.
        station : string
            Station code, e.g. 'MANZ'.
        datetime : :class:`~obspy.core.utcdatetime.UTCDateTime`
            Time for which the PAZ is requested, e.g. '2010-01-01 12:00:00'.
        location : string
            Location code, e.g. '00'.

        Returns
        -------
            Dictionary containing station coordinate information.
        """
        # NOTHING goes ABOVE this line!
        kwargs = {} #no **kwargs so use empty dict
        for key, value in locals().iteritems():
            if key not in ["self", "kwargs"]:
                kwargs[key] = value

        metadata = self.getList(**kwargs)
        if not metadata:
            msg = "No coordinates for station %s.%s at %s" % \
                    (network, station, datetime)
            raise Exception(msg)
        if len(metadata) > 1:
            warnings.warn("Received more than one metadata set. Using first.")
        metadata = metadata[0]
        coords = {}
        for key in ['latitude', 'longitude', 'elevation']:
            coords[key] = metadata[key]
        return coords

    @deprecated_keywords({'network_id':'network', 'station_id':'station',
                          'location_id':'location', 'channel_id':'channel'})
    def getPAZ(self, network, station, datetime, location='', channel='',
               seismometer_gain=False):
        """
        Get PAZ for a station at given time span. Gain is the A0 normalization
        constant for the poles and zeros.

        >>> c = Client()
        >>> a = c.station.getPAZ('BW', 'MANZ', '20090707', channel='EHZ')
        >>> a['zeros']
        [0j, 0j]
        >>> a['poles']
        [(-0.037004000000000002+0.037016j), (-0.037004000000000002-0.037016j), (-251.33000000000001+0j), (-131.03999999999999-467.29000000000002j), (-131.03999999999999+467.29000000000002j)]
        >>> a['gain']
        60077000.0
        >>> a['sensitivity']
        2516800000.0

        Parameters
        ----------
        network : string
            Network code, e.g. 'BW'.
        station : string
            Station code, e.g. 'MANZ'.
        datetime : :class:`~obspy.core.utcdatetime.UTCDateTime`
            Time for which the PAZ is requested, e.g. '2010-01-01 12:00:00'.
        location : string
            Location code, e.g. '00'.
        channel : string
            Channel code, e.g. 'EHE'.
        seismometer_gain : boolean, optional
            Adds seismometer gain to dictionary (default is True).

        Returns
        -------
            Dictionary containing zeros, poles, gain and sensitivity.
        """
        # request station information
        station_list = self.getList(network=network, station=station,
                                    datetime=datetime)
        if not station_list:
            return {}
        # don't allow wild cards - either search over exact one node or all
        for t in ['*', '?']:
            if t in channel:
                channel = ''
            if t in location:
                location = ''

        if len(station_list) > 1:
            warnings.warn("Received more than one PAZ file. Using first.")
        xml_doc = station_list[0]
        # request station resource
        res = self.client.station.getXMLResource(xml_doc['resource_name'])
        base_node = res.station_control_header
        # search for nodes with correct channel and location code
        if channel or location:
            # fetch next following response_poles_and_zeros node
            xpath_expr = "channel_identifier[channel_identifier='" + \
                channel + "' and location_identifier='" + location + \
                "']/following-sibling::response_poles_and_zeros"
            paz_node = base_node.xpath(xpath_expr)[0]
            # fetch next following channel_sensitivity_node with 
            # stage_sequence_number == 0
            xpath_expr = "channel_identifier[channel_identifier='" + \
                channel + "' and location_identifier='" + location + \
                "']/following-sibling::channel_sensitivity_" + \
                "gain[stage_sequence_number='0']"
            sensitivity_node = base_node.xpath(xpath_expr)[0]
            # fetch seismometer gain following channel_sensitivity_node with 
            # stage_sequence_number == 1
            xpath_expr = "channel_identifier[channel_identifier='" + \
                channel + "' and location_identifier='" + location + \
                "']/following-sibling::channel_sensitivity_" + \
                "gain[stage_sequence_number='1']"
            seismometer_gain_node = base_node.xpath(xpath_expr)[0]
        else:
            # just take first existing nodes
            paz_node = base_node.response_poles_and_zeros[0]
            sensitivity_node = base_node.channel_sensitivity_gain[-1]
            seismometer_gain_node = base_node.channel_sensitivity_gain[0]
        paz = {}
        # poles
        poles_real = paz_node.complex_pole.real_pole[:]
        poles_imag = paz_node.complex_pole.imaginary_pole[:]
        poles = zip(poles_real, poles_imag)
        paz['poles'] = [complex(p[0], p[1]) for p in poles]
        # zeros
        zeros_real = paz_node.complex_zero.real_zero[:][:]
        zeros_imag = paz_node.complex_zero.imaginary_zero[:][:]
        zeros = zip(zeros_real, zeros_imag)
        paz['zeros'] = [complex(z[0], z[1]) for z in zeros]
        # gain
        paz['gain'] = paz_node.A0_normalization_factor.pyval
        # sensitivity
        paz['sensitivity'] = sensitivity_node.sensitivity_gain.pyval
        # paz['name'] = name
        if seismometer_gain:
            paz['seismometer_gain'] = \
                seismometer_gain_node.sensitivity_gain.pyval
        return paz


class _EventMapperClient(_BaseRESTClient):
    """
    Event class to access the SeisHub event-mapper_

    .. _event-mapper: http://svn.geophysik.uni-muenchen.de/trac/seishub/browser/trunk/plugins/seishub.plugins.seismology/seishub/plugins/seismology/event.py
    """
    package = 'seismology'
    resourcetype = 'event'

    def getList(self, limit=None, offset=None, localization_method=None,
                account=None, user=None, min_datetime=None, max_datetime=None,
                first_pick=None, last_pick=None, min_latitude=None,
                max_latitude=None, min_longitude=None, max_longitude=None,
                min_magnitude=None, max_magnitude=None, min_depth=None,
                max_depth=None, used_p=None, min_used_p=None, max_used_p=None,
                used_s=None, min_used_s=None, max_used_s=None,
                document_id=None, **kwargs):
        """
        Gets a list of event information. 

        Returns
        -------
            List of dictionaries containing event information.
        """
        # NOTHING goes ABOVE this line!
        for key, value in locals().iteritems():
            if key not in ["self", "kwargs"]:
                kwargs[key] = value
        url = '/seismology/event/getList'
        root = self.client._objectify(url, **kwargs)
        return [dict(((k, v.pyval) for k, v in node.__dict__.iteritems())) \
                for node in root.getchildren()]

    @deprecated
    def getKml(self, nolabels=False, **kwargs):
        """
        Deprecated. Please use
        :meth:`~obspy.seishub.client._EventMapperClient.getKML()` instead.
        """
        return self.getKML(nolabels=nolabels, **kwargs)

    def getKML(self, nolabels=False, **kwargs):
        """
        Posts an event.getList() and returns the results as a KML file. For
        optional arguments, see documentation of
        :meth:`~obspy.seishub.client._EventMapperClient.getList()`
        
        :type nolabels: Boolean
        :param nolabels: Hide labels of events in KML. Can be useful with large
                data sets.
        Returns
        -------
            String containing KML information of all matching events. This
            string can be written to a file and loaded into e.g. Google Earth.
        """
        events = self.getList(**kwargs)
        timestamp = datetime.now()

        # construct the KML file
        kml = Element("kml")
        kml.set("xmlns", "http://www.opengis.net/kml/2.2")

        document = SubElement(kml, "Document")
        SubElement(document, "name").text = "Seishub Event Locations"

        # style definitions for earthquakes
        style = SubElement(document, "Style")
        style.set("id", "earthquake")

        iconstyle = SubElement(style, "IconStyle")
        SubElement(iconstyle, "scale").text = "0.5"
        icon = SubElement(iconstyle, "Icon")
        SubElement(icon, "href").text = \
            "http://maps.google.com/mapfiles/kml/shapes/earthquake.png"
        hotspot = SubElement(iconstyle, "hotSpot")
        hotspot.set("x", "0.5")
        hotspot.set("y", "0")
        hotspot.set("xunits", "fraction")
        hotspot.set("yunits", "fraction")

        labelstyle = SubElement(style, "LabelStyle")
        SubElement(labelstyle, "color").text = "ff0000ff"
        SubElement(labelstyle, "scale").text = "0.8"

        folder = SubElement(document, "Folder")
        SubElement(folder, "name").text = "SeisHub Events (%s)" % \
                                          timestamp.date()
        SubElement(folder, "open").text = "1"

        # additional descriptions for the folder
        descrip_str = "Fetched from: %s" % self.client.base_url
        descrip_str += "\nFetched at: %s" % timestamp
        descrip_str += "\n\nSearch options:\n"
        descrip_str += "\n".join(["=".join((str(k), str(v))) \
                                  for k, v in kwargs.items()])
        SubElement(folder, "description").text = descrip_str

        style = SubElement(folder, "Style")
        liststyle = SubElement(style, "ListStyle")
        SubElement(liststyle, "listItemType").text = "check"
        SubElement(liststyle, "bgColor").text = "00ffffff"
        SubElement(liststyle, "maxSnippetLines").text = "5"

        # add one marker per event
        interesting_keys = ['resource_name', 'localisation_method', 'account',
                            'user', 'public', 'datetime', 'longitude',
                            'latitude', 'depth', 'magnitude', 'used_p',
                            'used_s']
        for event_dict in events:
            placemark = SubElement(folder, "Placemark")
            date = str(event_dict['datetime']).split(" ")[0]
            mag = str(event_dict['magnitude'])

            # scale marker size to magnitude if this information is present
            if mag:
                mag = float(mag)
                label = "%s: %.1f" % (date, mag)
                try:
                    icon_size = 1.2 * log(1.5 + mag)
                except ValueError:
                    icon_size = 0.1
            else:
                label = date
                icon_size = 0.5
            if nolabels:
                SubElement(placemark, "name").text = ""
            else:
                SubElement(placemark, "name").text = label
            SubElement(placemark, "styleUrl").text = "#earthquake"
            style = SubElement(placemark, "Style")
            icon_style = SubElement(style, "IconStyle")
            liststyle = SubElement(style, "ListStyle")
            SubElement(liststyle, "maxSnippetLines").text = "5"
            SubElement(icon_style, "scale").text = str(icon_size)
            point = SubElement(placemark, "Point")
            SubElement(point, "coordinates").text = "%.10f,%.10f,0" % \
                    (event_dict['longitude'], event_dict['latitude'])

            # detailed information on the event for the description
            descrip_str = ""
            for key in interesting_keys:
                if not key in event_dict:
                    continue
                descrip_str += "\n%s: %s" % (key, event_dict[key])
            SubElement(placemark, "description").text = descrip_str

        # generate and return KML string
        return tostring(kml, pretty_print=True, xml_declaration=True)

    def saveKML(self, filename, overwrite=False, **kwargs):
        """
        Posts an event.getList() and writes the results as a KML file. For
        optional arguments, see help for
        :meth:`~obspy.seishub.client._EventMapperClient.getList()` and
        :meth:`~obspy.seishub.client._EventMapperClient.getKML()`
        
        :type filename: String
        :param filename: Filename (complete path) to save KML to.
        :type overwrite: Boolean
        :param overwrite: Overwrite existing file, otherwise if file exists an
                Exception is raised.
        :type nolabels: Boolean
        :param nolabels: Hide labels of events in KML. Can be useful with large
                data sets.
        :return: String containing KML information of all matching events. This
                 string can be written to a file and loaded into e.g. Google
                 Earth.
        """
        if not overwrite and os.path.lexists(filename):
            raise OSError("File %s exists and overwrite=False." % filename)
        kml_string = self.getKML(**kwargs)
        open(filename, "wt").write(kml_string)
        return


class RequestWithMethod(urllib2.Request):
    """
    Improved urllib2.Request Class for which the HTTP Method can be set to
    values other than only GET and POST.
    See http://benjamin.smedbergs.us/blog/2008-10-21/putting-and-deleteing-in-python-urllib2/
    """
    def __init__(self, method, *args, **kwargs):
        if method not in HTTP_ACCEPTED_METHODS:
            msg = "HTTP Method not supported. " + \
                  "Supported are: %s." % HTTP_ACCEPTED_METHODS
            raise ValueError(msg)
        urllib2.Request.__init__(self, *args, **kwargs)
        self._method = method

    def get_method(self):
        return self._method


if __name__ == '__main__':
    import doctest
    doctest.testmod(exclude_empty=True)
