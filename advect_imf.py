from matplotlib import pyplot as plt
from spacepy import datamodel as dm
from cdaweb import get_cdf

from cache_decorator import cache_result

from datetime import datetime, timedelta
import numpy as np

@cache_result(clear=False)
def load_acedata(tstart,tend, proxy=None):
    """
    Fetch ACE data from CDAWeb

    tstart: Desired start time
    tend: Desired end time

    Returns: A dictionary of tuples, each containing an array of times and an array of ACE observations for a particular variable
    """

    # Download SWEPAM and Mag data from CDAWeb
    swepam_data=get_cdf('sp_phys','AC_H0_SWE',tstart,tend,['Np','V_GSM','Tpr','SC_pos_GSM'], proxy=proxy)
    mag_data=get_cdf('sp_phys','AC_H0_MFI',tstart,tend,['BGSM'], proxy=proxy)

    # Dictionary to store all the data from ACE
    acedata={
        'T':(swepam_data['Epoch'],swepam_data['Tpr']),
        'rho':(swepam_data['Epoch'],swepam_data['Np']),
    }

    # Store all the vector data in the array
    for i,coord in enumerate('xyz'):
        for dataset,local_name,cdaweb_name in [(mag_data,'b','BGSM'),
                                 (swepam_data,'u','V_GSM'),
                                 (swepam_data,'','SC_pos_GSM')]:
            t,values=dataset['Epoch'],dataset[cdaweb_name][:,i]
            
            # Grab the appropriate component from
            # VALIDMIN and VALIDMAX attributes
            values.attrs['VALIDMIN']=values.attrs['VALIDMIN'][i]
            values.attrs['VALIDMAX']=values.attrs['VALIDMAX'][i]

            # Store in acedata dict
            acedata[local_name+coord]=t,values

    for var in acedata.keys():
        
        # Restrict to only valid data
        t_var,varIn=acedata[var]
        goodpoints=(varIn<varIn.attrs['VALIDMAX']) & (varIn>varIn.attrs['VALIDMIN'])
        t_var,varIn=t_var[goodpoints],varIn[goodpoints]

        acedata[var]=(t_var,varIn)
        
    return acedata
        

@cache_result(clear=False)
def load_dscovr(tstart,tend, proxy=None):
    """
    Fetch DSCOVR data from CDAWeb

    tstart: Desired start time
    tend: Desired end time

    Returns: A dictionary of tuples, each containing an array of times and an array of DSCOVR observations for a particular variable
    """

    # Download SWEPAM and Mag data from CDAWeb
    plasma_data=get_cdf('sp_phys','DSCOVR_H1_FC',tstart,tend,['Np','V_GSE','THERMAL_TEMP'], proxy=proxy)
    mag_data=get_cdf('sp_phys','DSCOVR_H0_MAG',tstart,tend,['B1GSE'], proxy=proxy)
    orbit_data=get_cdf('sp_phys','DSCOVR_ORBIT_PRE',tstart,tend,['GSE_POS'], proxy=proxy)

    # Dictionary to store all the data from DSCOVR
    dscovrdata={
        'T':(plasma_data['Epoch'],plasma_data['THERMAL_TEMP']),
        'rho':(plasma_data['Epoch'],plasma_data['Np']),
    }

    # Store all the vector data in the array
    for i,coord in enumerate('xyz'):
        for dataset,local_name,cdaweb_name,cdaweb_time_var in [
                (mag_data,'b','B1GSE','Epoch1'),
                (plasma_data,'u','V_GSE','Epoch'),
                (orbit_data,'','GSE_POS','Epoch')]:
            t,values=dataset[cdaweb_time_var],dataset[cdaweb_name][:,i]

            for attr in ('VALIDMIN','VALIDMAX'):

                try:
                    len(values.attrs[attr])
                except TypeError:
                    # It's a scalar, leave it as it is
                    pass
                else:
                    # Grab the appropriate component from
                    # VALIDMIN and VALIDMAX attributes
                    values.attrs[attr]=values.attrs[attr][0]

            # Store in data dict
            dscovrdata[local_name+coord]=t,values

    for var in dscovrdata.keys():
        
        # Restrict to only valid data
        t_var,varIn=dscovrdata[var]
        goodpoints= (varIn>varIn.attrs['VALIDMIN'])
        t_var,varIn=t_var[goodpoints],varIn[goodpoints]

        dscovrdata[var]=(t_var,varIn)
        
    return dscovrdata
        

def initialize(sw_data,advect_vars=['ux','uy','uz','bx','by','bz','rho','T'],ncells=1000,l1_x=1.6e6,output_x=0):
    """
    Initialize advection simulation
    
    sw_data: Dictionary of L1 solar wind data, structured in the form returned from load_acedata or load_dscovr
    advect_vars: Keys in the sw_data dictionary for variables that should be advected
    ncells: Number of cells in the computational grid
    l1_x: Maximum coordinate (in GSM/GSE x, units of km) of the upstream solar wind data
    output_x: Minimum coordinate (in GSM/GSE x, km) where output will be needed
    """

    l1data={}

    xextent=l1_x-output_x
    max_x=l1_x+xextent*(2./ncells)
    min_x=output_x-xextent*(2./ncells)

    # Make the grid
    x=np.linspace(min_x,max_x,ncells)

    # ux must be advected regardless
    if 'ux' not in advect_vars:
        advect_vars=list(advect_vars)+['ux']

    # Start time of simulation is first point for which all variables have valid data
    t0=np.max([t[0] for var,(t,values) in sw_data.items()])

    for var in sw_data.keys():

        t_var,values=sw_data[var]

        # Subtract epoch time from time arrays and convert them to seconds
        t_var=np.array([(t-t0).total_seconds() for t in t_var])

        l1data[var]=t_var,values

    # Initialize simulation state vectors
    state={var:np.ones([ncells])*values[0] for var,[t,values] in sw_data.items() if var in advect_vars}
    state['x']=x

    # Dictionary to hold output variables
    outdata={var:[] for var in advect_vars}
    outdata['time']=[]

    return state,outdata,t0,l1data

def iterate(state,t,outdata,sw_data,nuMax=0.5,output_x=0,limiter='Minmod'):
    """
    Advect L1 observations to Earth

    state: dictionary of state variables, as generated by initialize()
    t: Current time associated with state
    outdata: Dictionary to store the output data
    sw_data: Dictionary of L1 solar wind data, structured in the form returned from load_acedata or load_dscovr
    nuMax: Maximum allowed CFL
    output_x: x coordinate (in the GSM/GSE coordinate system with units of km) where output values should be provided
    """

    from advect1d import step, step_burgers, updateboundary

    u=state['ux']
    x=state['x']
    dx=x[1]-x[0]

    # Variables to be advected include everything in state except 'x'
    advect_vars = list(state.keys())
    advect_vars.remove('x')

    # Put ux on the end of advect_vars since it requires special treatment
    advect_vars.remove('ux')
    advect_vars.append('ux')

    # Update boundary conditions with values at new time step
    for var in advect_vars:
        
        a=state[var]
        var_t,values=sw_data[var]
        x_sat_t,x_sat=sw_data['x']
        updateboundary(a,t,x,x_sat,x_sat_t,values,var_t)
        
    # Find the time step
    dt=nuMax/np.abs(np.min(u))*dx
        
    # Step variables forward in time
    for var in advect_vars[:-1]:
        a=state[var]
        step(a,u,dx,dt,limiter)

    # ux handled separately since it has a different governing equation
    step_burgers(u,dx,dt,limiter)
    state['ux'][:]=u

    # Store output state
    for var in advect_vars:
        from scipy.interpolate import interp1d

        # Interpolate state variable to point where output is requested
        outdata[var].append(
            interp1d(x,state[var])(output_x)
        )

    # Append time to output state
    outdata['time'].append(t+dt)

    return dt

if __name__=='__main__':
    proxy = None #('proxy.example.edu:1406', 'https')
    # Fetch solar wind data
    sw_data=load_dscovr(datetime(2017,9,6,20),datetime(2017,9,7,5), proxy=proxy)

    output_x=0

    # Initialize the simulation state
    state,outdata,t0,l1data_tnum=initialize(sw_data,output_x=output_x)

    # Stop time of simulation is last point for which all variables have valid data
    tmax=np.min([t[-1] for var,(t,values) in l1data_tnum.items()])

    # Step forward in time
    t=0
    i=0
    while t<tmax:
        dt=iterate(state,t,outdata,l1data_tnum,output_x=output_x)
        t+=dt
        i+=1

    # Write output to disk
    import spacepy.datamodel as dm
    outhdf=dm.SpaceData()
    for key in outdata.keys():
        outhdf[key]=dm.dmarray(outdata[key])
    outhdf['time'].attrs['epoch']=t0.isoformat()
    outhdf.toHDF5('advected.h5')
