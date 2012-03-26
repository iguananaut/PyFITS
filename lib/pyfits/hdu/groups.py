import sys
import numpy as np

from pyfits.column import Column, ColDefs, FITS2NUMPY
from pyfits.fitsrec import FITS_rec, FITS_record
from pyfits.hdu.image import _ImageBaseHDU, PrimaryHDU
from pyfits.hdu.table import _TableLikeHDU
from pyfits.util import lazyproperty, _is_int, _pad_length, \
                        _is_pseudo_unsigned


class GroupsHDU(PrimaryHDU, _TableLikeHDU):
    """
    FITS Random Groups HDU class.
    """

    _width2format = {8: 'B', 16: 'I', 32: 'J', 64: 'K', -32: 'E', -64: 'D'}

    def __init__(self, data=None, header=None, name=None):
        """
        TODO: Write me
        """

        super(GroupsHDU, self).__init__(data=data, header=header)

        # The name of the table record array field that will contain the group
        # data for each group; 'data' by default, but may be precdeded by any
        # number of underscores if 'data' is already a parameter name
        self._data_field = 'DATA'

        # Update the axes; GROUPS HDUs should always have at least one axis
        if len(self._axes) <= 0:
            self._axes = [0]
            self._header['NAXIS'] = 1
            self._header.set('NAXIS1', 0, after='NAXIS')

    @classmethod
    def match_header(cls, header):
        keyword = header.cards[0].keyword
        return (keyword == 'SIMPLE' and 'GROUPS' in header and
                header['GROUPS'] == True)

    @lazyproperty
    def data(self):
        """
        The data of a random group FITS file will be like a binary table's
        data.
        """

        # Nearly the same code as in _TableBaseHDU
        if self.size:
            data = GroupData(self._get_tbdata())
            data._coldefs = self.columns
            data.formats = self.columns.formats
            data.parnames = self.parnames
            del self.columns
        else:
            data = None
        return data

    @lazyproperty
    def parnames(self):
        """The names of the group parameters as described by the header."""

        pcount = self._header['PCOUNT']
        # The FITS standard doesn't really say what to do if a parname is
        # missing, so for now just assume that won't happen
        return [self._header['PTYPE' + str(idx + 1)] for idx in range(pcount)]


    @lazyproperty
    def columns(self):
        if self._data_loaded and hasattr(self.data, '_coldefs'):
            return self.data._coldefs

        cols = []
        parnames = []
        format = self._width2format[self._header['BITPIX']]

        for idx in range(self._header['PCOUNT']):
            bscale = self._header.get('PSCAL' + str(idx + 1), 1)
            bzero = self._header.get('PZERO' + str(idx + 1), 0)
            name = self._header['PTYPE' + str(idx + 1)]
            parnames.append(name)
            cols.append(Column(name=name, format=format, bscale=bscale,
                        bzero=bzero))

        data_shape = self.shape[:-1]
        dat_format = str(int(np.array(data_shape).sum())) + format

        bscale = self._header.get('BSCALE', 1)
        bzero = self._header.get('BZERO', 0)

        parnames_upper = set(n.upper() for n in parnames)
        while self._data_field in parnames_upper:
            self._data_field = '_' + self._data_field

        cols.append(Column(name=self._data_field, format=dat_format,
                           bscale=bscale, bzero=bzero))
        coldefs = ColDefs(cols)
        # TODO: Something has to be done about this spaghetti code of arbitrary
        # attributes getting tacked on to the coldefs here.
        coldefs._shape = self._header['GCOUNT']
        coldefs._dat_format = FITS2NUMPY[format]
        return coldefs

    @lazyproperty
    def _theap(self):
        # Only really a lazyproperty for symmetry with _TableBaseHDU
        return 0

    @property
    def size(self):
        """
        Returns the size (in bytes) of the HDU's data part.
        """

        size = 0
        naxis = self._header.get('NAXIS', 0)

        # for random group image, NAXIS1 should be 0, so we skip NAXIS1.
        if naxis > 1:
            size = 1
            for idx in range(1, naxis):
                size = size * self._header['NAXIS' + str(idx + 1)]
            bitpix = self._header['BITPIX']
            gcount = self._header.get('GCOUNT', 1)
            pcount = self._header.get('PCOUNT', 0)
            size = abs(bitpix) * gcount * (pcount + size) // 8
        return size

    def update_header(self):
        old_naxis = self._header.get('NAXIS', 0)

        if self._data_loaded:
            if isinstance(self.data, GroupData):
                field0 = self.data.dtype.names[0]
                field0_code = self.data.dtype.fields[field0][0].name
                self._header['BITPIX'] = _ImageBaseHDU.ImgCode[field0_code]
                self._axes = list(self.data.data.shape)[1:]
                self._axes.reverse()
                self._axes = [0] + self._axes
            elif self.data is None:
                self._axes = []
            else:
                raise ValueError('incorrect array type')

        self._header['NAXIS'] = len(self._axes)

        # add NAXISi if it does not exist
        for idx, axis in enumerate(self._axes):
            try:
                self._header['NAXIS' + str(idx + 1)] = axis
            except KeyError:
                if (idx == 0):
                    after = 'NAXIS'
                else:
                    after = 'NAXIS' + str(idx)
                self._header.update('NAXIS' + str(idx + 1), axis, after=after)

        # delete extra NAXISi's
        for idx in range(len(self._axes) + 1, old_naxis + 1):
            try:
                del self._header['NAXIS' + str(idx)]
            except KeyError:
                pass

        if isinstance(self.data, GroupData):
            self._header.set('GROUPS', True,
                             after='NAXIS' + str(len(self._axes)))
            self._header.set('PCOUNT', len(self.data.parnames), after='GROUPS')
            self._header.set('GCOUNT', len(self.data), after='PCOUNT')
            npars = len(self.data.parnames)
            _scale, _zero = self.data._get_scale_factors(npars)[3:5]
            if _scale:
                self._header.set('BSCALE', self.data._coldefs.bscales[npars])
            if _zero:
                self._header.set('BZERO', self.data._coldefs.bzeros[npars])
            for idx in range(npars):
                self._header.set('PTYPE' + str(idx + 1),
                                 self.data.parnames[idx])
                _scale, _zero = self.data._get_scale_factors(idx)[3:5]
                if _scale:
                    self._header.set('PSCAL' + str(idx + 1),
                                     self.data._coldefs.bscales[idx])
                if _zero:
                    self._header.set('PZERO' + str(idx + 1),
                                     self.data._coldefs.bzeros[idx])

        # Update the position of the EXTEND keyword if it already exists
        if 'EXTEND' in self._header:
            if len(self._axes):
                after = 'NAXIS' + str(len(self._axes))
            else:
                after = 'NAXIS'
            self._header.set('EXTEND', after=after)

    def _get_tbdata(self):
        # get the right shape for the data part of the random group,
        # since binary table does not support ND yet
        self.columns._recformats[-1] = (repr(self.shape[:-1]) +
                                        self.columns._dat_format)

        return super(GroupsHDU, self)._get_tbdata()

    def _writedata_internal(self, fileobj):
        """
        Basically copy/pasted from `_ImageBaseHDU._writedata_internal()`, but
        we have to get the data's byte order a different way...

        TODO: Might be nice to store some indication of the data's byte order
        as an attribute or function so that we don't have to do this.
        """

        size = 0

        if self.data is not None:
            self.data._scale_back()

            # Based on the system type, determine the byteorders that
            # would need to be swapped to get to big-endian output
            if sys.byteorder == 'little':
                swap_types = ('<', '=')
            else:
                swap_types = ('<',)
            # deal with unsigned integer 16, 32 and 64 data
            if _is_pseudo_unsigned(self.data.dtype):
                # Convert the unsigned array to signed
                output = np.array(
                    self.data - _unsigned_zero(self.data.dtype),
                    dtype='>i%d' % self.data.dtype.itemsize)
                should_swap = False
            else:
                output = self.data
                fname = self.data.dtype.names[0]
                byteorder = self.data.dtype.fields[fname][0].str[0]
                should_swap = (byteorder in swap_types)

            if not fileobj.simulateonly:
                if should_swap:
                    output.byteswap(True)
                    try:
                        fileobj.writearray(output)
                    finally:
                        output.byteswap(True)
                else:
                    fileobj.writearray(output)

            size += output.size * output.itemsize
        return size

    def _verify(self, option='warn'):
        errs = super(GroupsHDU, self)._verify(option=option)

        # Verify locations and values of mandatory keywords.
        self.req_cards('NAXIS', 2,
                       lambda v: (_is_int(v) and v >= 1 and v <= 999), 1,
                       option, errs)
        self.req_cards('NAXIS1', 3, lambda v: (_is_int(v) and v == 0), 0,
                       option, errs)

        after = self._header['NAXIS'] + 3
        pos = lambda x: x >= after

        self.req_cards('GCOUNT', pos, _is_int, 1, option, errs)
        self.req_cards('PCOUNT', pos, _is_int, 0, option, errs)
        self.req_cards('GROUPS', pos, lambda v: (v is True), True, option,
                       errs)
        return errs

    def _calculate_datasum(self, blocking):
        """
        Calculate the value for the ``DATASUM`` card in the HDU.
        """

        if self._data_loaded and self.data is not None:
            # We have the data to be used.
            # Check the byte order of the data.  If it is little endian we
            # must swap it before calculating the datasum.
            byteorder = \
                 self.data.dtype.fields[self.data.dtype.names[0]][0].str[0]

            if byteorder != '>':
                byteswapped = True
                d = self.data.byteswap(True)
                d.dtype = d.dtype.newbyteorder('>')
            else:
                byteswapped = False
                d = self.data

            cs = self._compute_checksum(np.fromstring(d, dtype='ubyte'),
                                        blocking=blocking)

            # If the data was byteswapped in this method then return it to
            # its original little-endian order.
            if byteswapped:
                d.byteswap(True)
                d.dtype = d.dtype.newbyteorder('<')

            return cs
        else:
            # This is the case where the data has not been read from the file
            # yet.  We can handle that in a generic manner so we do it in the
            # base class.  The other possibility is that there is no data at
            # all.  This can also be handled in a gereric manner.
            return super(GroupsHDU, self)._calculate_datasum(blocking=blocking)

    def _summary(self):
        summary = super(GroupsHDU, self)._summary()
        name, classname, length, shape, format, gcount = summary

        # Drop the first axis from the shape
        if shape:
            shape = shape[1:]

        if self._data_loaded:
            # Update the format
            format = self.data.dtype.fields[self.data.dtype.names[0]][0].name
            format = format[format.rfind('.') + 1:]

        # Update the GCOUNT report
        gcount = '%d Groups  %d Parameters' % (self._gcount, self._pcount)
        return (name, classname, length, shape, format, gcount)


class Group(FITS_record):
    """
    One group of the random group data.
    """

    def __init__(self, input, row):
        super(Group, self).__init__(input, row)
        self.parnames = input.parnames

    def __str__(self):
        """
        Print one row.
        """

        if isinstance(self.row, slice):
            if self.row.step:
                step = self.row.step
            else:
                step = 1

            if self.row.stop > len(self.array):
                stop = len(self.array)
            else:
                stop = self.row.stop

            outlist = []

            for idx in range(self.row.start, stop, step):
                rowlist = []

                for jdx in range(self.array._nfields):
                    rowlist.append(repr(self.array.field(jdx)[idx]))

                outlist.append(' (%s)' % ', '.join(rowlist))

            return '[%s]' % ',\n'.join(outlist)
        else:
            return super(Group, self).__str__()

    @property
    def data(self):
        # The last column in the coldefs is the data portion of the group
        return self.field(self.array._coldefs.names[-1])

    @lazyproperty
    def _unique(self):
        return _unique([p.lower() for p in self.parnames])

    def par(self, parname):
        """
        Get the group parameter value.
        """

        if _is_int(parname):
            result = self.array[self.row][parname]
        else:
            indx = self._unique[parname.lower()]
            if len(indx) == 1:
                result = self.array[self.row][indx[0]]

            # if more than one group parameter have the same name
            else:
                result = self.array[self.row][indx[0]].astype('f8')
                for i in indx[1:]:
                    result += self.array[self.row][i]

        return result

    def setpar(self, parname, value):
        """
        Set the group parameter value.
        """

        if _is_int(parname):
            self.array[self.row][parname] = value
        else:
            indx = self._unique[parname.lower()]
            if len(indx) == 1:
                self.array[self.row][indx[0]] = value

            # if more than one group parameter have the same name, the
            # value must be a list (or tuple) containing arrays
            else:
                if isinstance(value, (list, tuple)) and \
                   len(indx) == len(value):
                    for i in range(len(indx)):
                        self.array[self.row][indx[i]] = value[i]
                else:
                    raise ValueError('Parameter value must be a sequence '
                                     'with %d arrays/numbers.' % len(indx))


class GroupData(FITS_rec):
    """
    Random groups data object.

    Allows structured access to FITS Group data in a manner analogous
    to tables.
    """

    _record_type = Group

    def __new__(subtype, input=None, bitpix=None, pardata=None, parnames=[],
                bscale=None, bzero=None, parbscales=None, parbzeros=None):
        """
        Parameters
        ----------
        input : array or FITS_rec instance
            input data, either the group data itself (a
            `numpy.ndarray`) or a record array (`FITS_rec`) which will
            contain both group parameter info and the data.  The rest
            of the arguments are used only for the first case.

        bitpix : int
            data type as expressed in FITS ``BITPIX`` value (8, 16, 32,
            64, -32, or -64)

        pardata : sequence of arrays
            parameter data, as a list of (numeric) arrays.

        parnames : sequence of str
            list of parameter names.

        bscale : int
            ``BSCALE`` of the data

        bzero : int
            ``BZERO`` of the data

        parbscales : sequence of int
            list of bscales for the parameters

        parbzeros : sequence of int
            list of bzeros for the parameters
        """

        if not isinstance(input, FITS_rec):
            _formats = ''
            _cols = []
            if pardata is None:
                npars = 0
            else:
                npars = len(pardata)

            if parbscales is None:
                parbscales = [None] * npars
            if parbzeros is None:
                parbzeros = [None] * npars

            if bitpix is None:
                bitpix = _ImageBaseHDU.ImgCode[input.dtype.name]
            fits_fmt = GroupsHDU._width2format[bitpix]  # -32 -> 'E'
            _fmt = FITS2NUMPY[fits_fmt]  # 'E' -> 'f4'
            _formats = (_fmt + ',') * npars
            data_fmt = '%s%s' % (str(input.shape[1:]), _fmt)
            _formats += data_fmt
            gcount = input.shape[0]
            for idx in range(npars):
                _cols.append(Column(name='c' + str(idx + 1), format=fits_fmt,
                                    bscale=parbscales[idx],
                                    bzero=parbzeros[idx]))
            _cols.append(Column(name='data', format=fits_fmt, bscale=bscale,
                                bzero=bzero))
            _coldefs = ColDefs(_cols)

            self = FITS_rec.__new__(subtype,
                                    np.rec.array(None,
                                                 formats=_formats,
                                                 names=_coldefs.names,
                                                 shape=gcount))
            self._coldefs = _coldefs
            self.parnames = parnames

            for idx in range(npars):
                _scale, _zero = self._get_scale_factors(idx)[3:5]
                if _scale or _zero:
                    self._convert[idx] = pardata[idx]
                else:
                    np.rec.recarray.field(self, idx)[:] = pardata[idx]
            _scale, _zero = self._get_scale_factors(npars)[3:5]
            if _scale or _zero:
                self._convert[npars] = input
            else:
                np.rec.recarray.field(self, npars)[:] = input
        else:
            self = FITS_rec.__new__(subtype, input)
            self.parnames = None
        return self

    def __array_finalize__(self, obj):
        super(GroupData, self).__array_finalize__(obj)
        if isinstance(obj, GroupData):
            self.parnames = obj.parnames
        elif isinstance(obj, FITS_rec):
            self.parnames = obj._coldefs.names

    def __getitem__(self, key):
        out = super(GroupData, self).__getitem__(key)
        if isinstance(out, GroupData):
            out.parnames = self.parnames
        return out

    @property
    def data(self):
        # The last column in the coldefs is the data portion of the group
        return self.field(self._coldefs.names[-1])

    @lazyproperty
    def _unique(self):
        return _unique([p.lower() for p in self.parnames])

    def par(self, parname):
        """
        Get the group parameter values.
        """

        if _is_int(parname):
            result = self.field(parname)
        else:
            indx = self._unique[parname.lower()]
            if len(indx) == 1:
                result = self.field(indx[0])

            # if more than one group parameter have the same name
            else:
                result = self.field(indx[0]).astype('f8')
                for i in indx[1:]:
                    result += self.field(i)

        return result


def _unique(names, casesensitive=False):
    """
    Given a list of objects, returns a mapping of objects in that list to the
    index or indices at which that object was found in the list.
    """

    unique = {}
    for idx, name in enumerate(names):
        if not casesensitive:
            name = name.lower()
        if name in unique:
            unique[name].append(idx)
        else:
            unique[name] = [idx]
    return unique
