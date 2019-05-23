import logging
import operator as OP
from contextlib import closing
from functools import reduce

from cached_property import cached_property

import dolfin

from ..io import h5yaml
from ..io.csv import LineCutCsvPlot
from ..util import SetattrInitMixin


class OutputWriter(SetattrInitMixin):
    '''Write output data extracted from a solution object.

    The output can be any of a plot  on a mesh, on a 1D linecut,
    or in a csv file containing data from multiple solutions
    during a parameter sweep.

    filename_prefix: a prefix to any filenames that will be saved.

    plot_mesh: Save data on the original mesh.

    plot_1d: Extract data along a 1D line cut and save to a csv file.

    plot_iv: Save terminal currents and voltages, as well as other
    data such as generation rates integrated over the mesh volume.
    '''

    @cached_property
    def meta_extractors(self):
        return []

    def format_parameter(self, solution, parameter_value):
        return '{:.14g}'.format(parameter_value)

    def get_plot_prefix(self, solution, parameter_value):
        return (self.filename_prefix +
                ' parameter={} csvplot'.format(
                    self.format_parameter(solution, parameter_value)))

    def get_iv_prefix(self, solution, parameter_value):
        return self.filename_prefix

    def write_output(self, solution, parameter_value):
        meta = {}

        for extractor in self.meta_extractors:
            extractor(
                solution=solution, parameter_value=parameter_value,
                output_writer=self, meta=meta).call()

        if self.plot_1d:
            plot_prefix = self.get_plot_prefix(solution, parameter_value)
            with closing(LineCutCsvPlot(
                    plot_prefix + '.csv', None)) as plotter:
                solution_plot_1d(plotter, solution, 0)
            h5yaml.dump(meta, plot_prefix + '.plot_meta.yaml')

        if self.plot_iv:
            if self.iv_writer is None:
                self.iv_writer = WriteIVFile(self.get_iv_prefix(
                    solution, parameter_value) + '.csv', solution)
            self.iv_writer.write_row(solution)

def _ensure_dict(d, k):
    v = d.get(k, None)
    if v is None:
        v = d[k] = {}
    return v

class MetaExtractorBandInfo(SetattrInitMixin):
    def call(self):
        band_info = _ensure_dict(self.meta, 'band_info')
        for k, band in self.solution.pdd.bands.items():
            bi = _ensure_dict(band_info, k)
            bi.update(sign=band.sign)

class MetaExtractorIntegrals(SetattrInitMixin):
    '''
Attributes
----------
facets: dict
    Facet regions where to extract quantities.
cells: dict
    Cell regions where to extract quantities.
solution:
    Solution object.
parameter_value:
    Parameter value.
meta:
    Metadata dictionary to write to.
'''
    def call(self):
        mu = self.pdd.mesh_util
        for k, b in self.pdd.bands.items():
            self.add_surface_flux(
                'avg_j_{}'.format(k), b.j, average=True,
                internal=False,
                units='mA/cm^2')
            self.add_surface_flux(
                'tot_j_{}'.format(k), b.j, average=False,
                internal=False,
                units='mA')
            self.add_volume_total(
                'avg_g_{}'.format(k), b.g, average=True,
                units='cm^-3 / s')
            self.add_volume_total(
                'tot_g_{}'.format(k), b.g, average=False,
                units='1 / s')

    @cached_property
    def pdd(self):
        return self.solution.pdd

    @cached_property
    def meta_integrals(self):
        return _ensure_dict(self.meta, 'integrals')

    def add_quantity(self, name, location_name, value):
        self.meta_integrals[':'.join((name, location_name))] = value

    def add_surface_total(
            self, k, expr, internal=True, external=True,
            average=False, units=None):
        mu = self.pdd.mesh_util
        for reg_name, reg in self.facets.items():
            dsS = mu.region_dsS(reg, internal=internal, external=external)
            value = mu.assemble(dsS*expr)
            if average:
                value = value / mu.assemble(dsS.abs()*mu.Constant(1.0))
            self.add_quantity(k, reg_name, value.m_as(units))

    def add_surface_flux(self, k, expr, **kwargs):
        mu = self.pdd.mesh_util
        flux = mu.dot(expr, mu.n)
        self.add_surface_total(k, flux, **kwargs)

    def add_volume_total(self, k, expr, average=False, units=None):
        mu = self.pdd.mesh_util
        for reg_name, cregion in self.cells.items():
            dx = mu.region_dx(cregion)
            value = mu.assemble(dx*expr)
            if average:
                value = value / mu.assemble(dx*mu.Constant(1.0))
            self.add_quantity(k, reg_name, value.m_as(units))

def solution_plot_1d(plotter, s, timestep, solver=None):
    plotter.new(timestep)
    pdd = s.pdd
    mesh = pdd.mesh_util.mesh
    po = pdd.poisson
    ur = s.unit_registry
    mu = pdd.mesh_util

    CG1 = mu.space.CG1
    DG0 = mu.space.DG0
    DG1 = mu.space.DG1
    DG2 = mu.space.DG2
    VCG1 = mu.space.vCG1
    add = plotter.add

    Vunit = ur.V
    Eunit = ur.V/ur.mesh_unit

    eV = ur.eV
    conc = 1/ur.cm**3
    econc = ur.elementary_charge*conc
    junit = ur.mA/ur.cm**2
    Iunit = 1/ur.cm**2/ur.s
    αunit = 1/ur.cm
    gunit = conc/ur.s

    if 1:
        add('E', Eunit, po.E, VCG1)
        add('phi', ur.V, po.phi, DG1)
        add('thmeq_phi', ur.V, po.thermal_equilibrium_phi, DG1)
        add('rho', econc, po.rho, DG1)
        add('static_rho', econc, po.static_rho, DG1)

    if 1:
        jays = []
        for k, band in pdd.bands.items():
            add('u_'+k, conc, band.u, DG1)
            add('thmeq_u_'+k, conc, band.thermal_equilibrium_u, DG1)
            add('qfl_'+k, eV, band.qfl, DG2)
            add('g_'+k, conc/ur.s, band.g, DG1)
            for procname, proc in pdd.electro_optical_processes.items():
                add('g_{}_{}'.format(procname, k), conc/ur.s,
                    proc.get_generation(band), DG1)
            add('j_'+k, junit, band.j, VCG1)
            add('mobility_'+k, ur('cm^2/V/s'), band.mobility, DG1)
            jays.append(band.j)
            if hasattr(band, 'energy_level'):
                E = band.energy_level
                ephi = po.phi*ur.elementary_charge
                add('E_'   +k, eV, E, DG1)
                add('Ephi_'+k, eV, E - ephi, DG1)
                del E, ephi
            if hasattr(band, 'mixedqfl_base_w'):
                add('w_{}_base'.format(k), eV, band.mixedqfl_base_w, DG2)
                add('w_{}_delta'.format(k), eV, band.mixedqfl_delta_w, DG2)

        add('j_tot', junit, reduce(OP.add, jays), VCG1)

    omu = s.optical.mesh_util
    oCG1 = omu.space.CG1

    if 1:
        for k, o in s.optical.fields.items():
            add('Phi_'+k, ur('1/cm^2/s'), o.Phi, oCG1)
            add('opt_g_'+k, ur('1/cm^3/s'), o.g, oCG1)
            add('opt_alpha_'+k, ur('1/cm'), o.alpha, oCG1)

def get_contact_currents(solution, contact_name):
    '''Integrate current flux across the area of a contact.

    Returns the electron and hole currents as Pint quantities.'''

    sl = solution

    # components of electron and hole currents normal to mesh facets
    jvn = sl['/pde/dot'](sl['/CB/j'], dolfin.FacetNormal(sl['/mesh']))
    jvp = sl['/pde/dot'](sl['/VB/j'], dolfin.FacetNormal(sl['/mesh']))
    int_over_surf = sl['/pde/integrate_over_surface']
    hole_current = int_over_surf(jvp, sl['/geometry/facet_regions/' + contact_name + '/ds'])
    elec_current = int_over_surf(jvn, sl['/geometry/facet_regions/' + contact_name + '/ds'])

    return elec_current, hole_current


class WriteIVFile(object):

    def __init__(self, filename, solution):
        from csv import writer
        iv_file = open(filename, 'w')
        self.iv_writer = writer(iv_file)

        # This is ugly... is there a better way?
        self.contacts = [n.split('/')[-1] for n in solution['/poisson/V_contact_bc_values'].keys()]
        column_names = []
        column_units = []

        for c in self.contacts:
            cname = c
            column_names.extend([cname + '_voltage', cname + '_elec_current',
                                 cname + '_hole_current'])
            if len(column_units) == 0:
                units = ['# V', 'A', 'A']
            else:
                units = ['V', 'A', 'A']
            column_units.extend(units)

        self.rate_quantities = ['r_cv', 'r_ci', 'r_iv', 'og_cv', 'og_ci', 'og_iv']
        column_names.extend(self.rate_quantities)
        column_units.extend(['1/s']*len(self.rate_quantities))

        self.iv_writer.writerow(column_names)
        self.iv_writer.writerow(column_units)

    def write_row(self, solution):
        sl = solution
        u = sl['/unit_registry']
        avg_over_surf = sl['/pde/average_over_surface']

        row = []

        for c in self.contacts:
            cname = c.split('/')[-1]
            ec, hc = [i.m_as(u.A) for i in get_contact_currents(solution, cname)]
            v = avg_over_surf(sl['/common/' + cname + '_bias'],
                              sl['/geometry/facet_regions/' + cname + '/ds']).m_as(u.V)
            row.extend([v, ec, hc])

        for r in self.rate_quantities:
            row.append(sl['/pde/assemble'](sl['/pde/dx']*sl['/strbg/' + r]).m_as(1./u.s))
        self.iv_writer.writerow(row)