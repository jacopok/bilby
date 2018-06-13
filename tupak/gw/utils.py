import logging
import os

import numpy as np
from gwpy.signal import filter_design
from gwpy.timeseries import TimeSeries
from scipy import signal

from tupak.core.utils import gps_time_to_gmst, ra_dec_to_theta_phi, speed_of_light, nfft


def asd_from_freq_series(freq_data, df):
    """
    Calculate the ASD from the frequency domain output of gaussian_noise()
    Input:
    freq_data - array of complex frequency domain data
    df - spacing of freq_data, 1/(segment length) used to generate the gaussian noise
    Output:
    asd = array of real-valued normalized frequency domain ASD data
    """
    asd = np.absolute(freq_data) * 2 * df**0.5
    return asd


def psd_from_freq_series(freq_data, df):
    """
    Calculate the PSD from the frequency domain output of gaussian_noise()
    Calls asd_from_freq_series() and squares the output
    Input:
    freq_data - array of complex frequency domain data
    df - spacing of freq_data, 1/(segment length) used to generate the gaussian noise
    Output:
    psd - array of real-valued normalized frequency domain PSD data
    """
    psd = np.power(asd_from_freq_series(freq_data, df), 2)
    return psd


def time_delay_geocentric(detector1, detector2, ra, dec, time):
    """
    Calculate time delay between two detectors in geocentric coordinates based on XLALArrivaTimeDiff in TimeDelay.c
    Input:
    detector1 - cartesian coordinate vector for the first detector in the geocentric frame
                generated by the Interferometer class as self.vertex
    detector2 - cartesian coordinate vector for the second detector in the geocentric frame
    To get time delay from Earth center, use detector2 = np.array([0,0,0])
    ra - right ascension of the source in radians
    dec - declination of the source in radians
    time - GPS time in the geocentric frame
    Output:
    delta_t - time delay between the two detectors in the geocentric frame
    """
    gmst = gps_time_to_gmst(time)
    theta, phi = ra_dec_to_theta_phi(ra, dec, gmst)
    omega = np.array([np.sin(theta) * np.cos(phi), np.sin(theta) * np.sin(phi), np.cos(theta)])
    delta_d = detector2 - detector1
    delta_t = np.dot(omega, delta_d) / speed_of_light
    return delta_t


def get_polarization_tensor(ra, dec, time, psi, mode):
    """
    Calculate the polarization tensor for a given sky location and time

    See Nishizawa et al. (2009) arXiv:0903.0528 for definitions of the polarisation tensors.
    [u, v, w] represent the Earth-frame
    [m, n, omega] represent the wave-frame
    Note: there is a typo in the definition of the wave-frame in Nishizawa et al.

    :param ra: right ascension in radians
    :param dec: declination in radians
    :param time: geocentric GPS time
    :param psi: binary polarisation angle counter-clockwise about the direction of propagation
    :param mode: polarisation mode
    :return: polarization_tensor(ra, dec, time, psi, mode): polarization tensor for the specified mode.
    """
    greenwich_mean_sidereal_time = gps_time_to_gmst(time)
    theta, phi = ra_dec_to_theta_phi(ra, dec, greenwich_mean_sidereal_time)
    u = np.array([np.cos(phi) * np.cos(theta), np.cos(theta) * np.sin(phi), -np.sin(theta)])
    v = np.array([-np.sin(phi), np.cos(phi), 0])
    m = -u * np.sin(psi) - v * np.cos(psi)
    n = -u * np.cos(psi) + v * np.sin(psi)

    if mode.lower() == 'plus':
        return np.einsum('i,j->ij', m, m) - np.einsum('i,j->ij', n, n)
    elif mode.lower() == 'cross':
        return np.einsum('i,j->ij', m, n) + np.einsum('i,j->ij', n, m)
    elif mode.lower() == 'breathing':
        return np.einsum('i,j->ij', m, m) + np.einsum('i,j->ij', n, n)

    omega = np.cross(m, n)
    if mode.lower() == 'longitudinal':
        return np.sqrt(2) * np.einsum('i,j->ij', omega, omega)
    elif mode.lower() == 'x':
        return np.einsum('i,j->ij', m, omega) + np.einsum('i,j->ij', omega, m)
    elif mode.lower() == 'y':
        return np.einsum('i,j->ij', n, omega) + np.einsum('i,j->ij', omega, n)
    else:
        logging.warning("{} not a polarization mode!".format(mode))
        return None


def get_vertex_position_geocentric(latitude, longitude, elevation):
    """
    Calculate the position of the IFO vertex in geocentric coordiantes in meters.

    Based on arXiv:gr-qc/0008066 Eqs. B11-B13 except for the typo in the definition of the local radius.
    See Section 2.1 of LIGO-T980044-10 for the correct expression
    """
    semi_major_axis = 6378137  # for ellipsoid model of Earth, in m
    semi_minor_axis = 6356752.314  # in m
    radius = semi_major_axis**2 * (semi_major_axis**2 * np.cos(latitude)**2
                                   + semi_minor_axis**2 * np.sin(latitude)**2)**(-0.5)
    x_comp = (radius + elevation) * np.cos(latitude) * np.cos(longitude)
    y_comp = (radius + elevation) * np.cos(latitude) * np.sin(longitude)
    z_comp = ((semi_minor_axis / semi_major_axis)**2 * radius + elevation) * np.sin(latitude)
    return np.array([x_comp, y_comp, z_comp])


def inner_product(aa, bb, frequency, PSD):
    """
    Calculate the inner product defined in the matched filter statistic

    arguments:
    aai, bb: single-sided Fourier transform, created, e.g., by the nfft function above
    frequency: an array of frequencies associated with aa, bb, also returned by nfft
    PSD: PSD object

    Returns:
    The matched filter inner product for aa and bb
    """
    PSD_interp = PSD.power_spectral_density_interpolated(frequency)

    # calculate the inner product
    integrand = np.conj(aa) * bb / PSD_interp

    df = frequency[1] - frequency[0]
    integral = np.sum(integrand) * df

    product = 4. * np.real(integral)

    return product


def noise_weighted_inner_product(aa, bb, power_spectral_density, time_duration):
    """
    Calculate the noise weighted inner product between two arrays.

    Parameters
    ----------
    aa: array
        Array to be complex conjugated
    bb: array
        Array not to be complex conjugated
    power_spectral_density: array
        Power spectral density
    time_duration: float
        time_duration of the data

    Return
    ------
    Noise-weighted inner product.
    """

    # caluclate the inner product
    integrand = np.conj(aa) * bb / power_spectral_density
    product = 4 / time_duration * np.sum(integrand)
    return product


def matched_filter_snr_squared(signal, interferometer, time_duration):
    return noise_weighted_inner_product(signal, interferometer.data, interferometer.power_spectral_density_array,
                                        time_duration)


def optimal_snr_squared(signal, interferometer, time_duration):
    return noise_weighted_inner_product(signal, signal, interferometer.power_spectral_density_array, time_duration)


def get_event_time(event):
    """
    Get the merger time for known GW events.

    We currently know about:
        GW150914
        LVT151012
        GW151226
        GW170104
        GW170608
        GW170814
        GW170817

    Parameters
    ----------
    event: str
        Event descriptor, this can deal with some prefixes, e.g., '150914', 'GW150914', 'LVT151012'

    Return
    ------
    event_time: float
        Merger time
    """
    event_times = {'150914': 1126259462.422, '151012': 1128678900.4443,  '151226': 1135136350.65,
                   '170104': 1167559936.5991, '170608': 1180922494.4902, '170814': 1186741861.5268,
                   '170817': 1187008882.4457}
    if 'GW' or 'LVT' in event:
        event = event[-6:]

    try:
        event_time = event_times[event[-6:]]
        return event_time
    except KeyError:
        print('Unknown event {}.'.format(event))
        return None


def get_open_strain_data(
        name, t1, t2, outdir, cache=False, raw_data_file=None, **kwargs):
    """ A function which accesses the open strain data

    This uses `gwpy` to download the open data and then saves a cached copy for
    later use

    Parameters
    ----------
    name: str
        The name of the detector to get data for
    t1, t2: float
        The GPS time of the start and end of the data
    outdir: str
        The output directory to place data in
    cache: bool
        If true, cache the data
    **kwargs:
        Passed to `gwpy.timeseries.TimeSeries.fetch_open_data`
    raw_data_file

    Returns
    -----------
    strain: gwpy.timeseries.TimeSeries

    """
    filename = '{}/{}_{}_{}.txt'.format(outdir, name, t1, t2)
    if raw_data_file:
        logging.info('Using raw_data_file {}'.format(raw_data_file))
        strain = TimeSeries.read(raw_data_file)
        if (t1 > strain.times[0].value) and (t2 < strain.times[-1].value):
            logging.info('Using supplied raw data file')
            strain = strain.crop(t1, t2)
        else:
            raise ValueError('Supplied file does not contain requested data')
    elif os.path.isfile(filename) and cache:
        logging.info('Using cached data from {}'.format(filename))
        strain = TimeSeries.read(filename)
    else:
        logging.info('Fetching open data ...')
        strain = TimeSeries.fetch_open_data(name, t1, t2, **kwargs)
        logging.info('Saving data to {}'.format(filename))
        strain.write(filename)
    return strain


def read_frame_file(file_name, t1, t2, channel=None, **kwargs):
    """ A function which accesses the open strain data

    This uses `gwpy` to download the open data and then saves a cached copy for
    later use

    Parameters
    ----------
    file_name: str
        The name of the frame to read
    t1, t2: float
        The GPS time of the start and end of the data
    channel: str
        The name of the channel being searched for, some standard channel names are attempted
        if channel is not specified or if specified channel is not found.
    **kwargs:
        Passed to `gwpy.timeseries.TimeSeries.fetch_open_data`

    Returns
    -----------
    strain: gwpy.timeseries.TimeSeries

    """
    loaded = False
    if channel is not None:
        try:
            strain = TimeSeries.read(source=file_name, channel=channel, start=t1, end=t2, **kwargs)
            loaded = True
            logging.info('Successfully loaded {}.'.format(channel))
        except RuntimeError:
            logging.warning('Channel {} not found. Trying preset channel names'.format(channel))
    for channel_type in ['GDS-CALIB_STRAIN', 'DCS-CALIB_STRAIN_C01', 'DCS-CALIB_STRAIN_C02']:
        for ifo_name in ['H1', 'L1']:
            channel = '{}:{}'.format(ifo_name, channel_type)
            if loaded:
                continue
            try:
                strain = TimeSeries.read(source=file_name, channel=channel, start=t1, end=t2, **kwargs)
                loaded = True
                logging.info('Successfully loaded {}.'.format(channel))
            except RuntimeError:
                None

    if loaded:
        return strain
    else:
        logging.warning('No data loaded.')
        return None


def process_strain_data(
        strain, alpha=0.25, filter_freq=1024, **kwargs):
    """
    Helper function to obtain an Interferometer instance with appropriate
    PSD and data, given an center_time.

    Parameters
    ----------
    name: str
        Detector name, e.g., 'H1'.
    center_time: float
        GPS time of the center_time about which to perform the analysis.
        Note: the analysis data is from `center_time-T/2` to `center_time+T/2`.
    T: float
        The total time (in seconds) to analyse. Defaults to 4s.
    alpha: float
        The tukey window shape parameter passed to `scipy.signal.tukey`.
    psd_offset, psd_duration: float
        The power spectral density (psd) is estimated using data from
        `center_time+psd_offset` to `center_time+psd_offset + psd_duration`.
    outdir: str
        Directory where the psd files are saved
    plot: bool
        If true, create an ASD + strain plot
    filter_freq: float
        Low pass filter frequency
    **kwargs:
        All keyword arguments are passed to
        `gwpy.timeseries.TimeSeries.fetch_open_data()`.

    Returns
    -------
    interferometer: `tupak.detector.Interferometer`
        An Interferometer instance with a PSD and frequency-domain strain data.

    """

    sampling_frequency = int(strain.sample_rate.value)

    # Low pass filter
    bp = filter_design.lowpass(filter_freq, strain.sample_rate)
    strain = strain.filter(bp, filtfilt=True)
    strain = strain.crop(*strain.span.contract(1))

    time_series = strain.times.value
    time_duration = time_series[-1] - time_series[0]

    # Apply Tukey window
    N = len(time_series)
    strain = strain * signal.windows.tukey(N, alpha=alpha)

    frequency_domain_strain, frequencies = nfft(strain.value, sampling_frequency)

    return frequency_domain_strain, frequencies