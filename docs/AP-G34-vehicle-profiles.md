# AP-G34-23 vehicle profiles

OpenArch Swept Paths seeds editable kinematic profiles from the vehicle dimension
figures in the user-supplied *Design Vehicles and Turning Path Templates Guide*,
Edition 4.0 (Austroads, 2023), Figures 2.1–2.13. The profile data is maintained in
`src/openarchsp/standard_profiles.py`; the guide and its drawing files are not
bundled or redistributed with the application.

## What is represented

The initial profile set covers the passenger vehicle, service vehicle, single-unit
truck/bus, long rigid bus, passenger vehicle with trailer, 19 m semi-trailer, 25 m
long semi-trailer, 25 m and 26 m B-doubles, B-triple, A-double and A-triple. Each
profile has body extents, axle stations and coupling points so the kinematics can
track a multi-unit combination. The articulated bus remains an editable/custom
geometry case because its front section and articulation arrangement does not match
the solver's steerable lead unit followed by towed units.

## Source and assumptions

- Overall lengths, body dimensions, axle spacing and longitudinal stations were
  transcribed from the corresponding AP-G34-23 figures.
- Where the guide shows an axle group but does not dimension the lateral wheel
  track, OpenArch estimates track from the schematic drawing.
- Coupling offsets in the multi-trailer profiles are reconstructed from the
  diagram's dimension chains. They should be checked against the applicable
  vehicle configuration and the supplied DWG template before design use.
- The guide specifies a 6 second steering lock-to-lock time and a 70 degree
  maximum articulation angle where applicable. These are the application defaults.
  It does not specify a maximum road-wheel steering angle for every vehicle;
  OpenArch currently uses a provisional 35 degree default that can be edited in
  the vehicle geometry JSON.
- The model is forward, low-speed and no-lateral-slip. These profiles are not a
  PBS assessment, a statutory approval, or a substitute for jurisdiction-specific
  vehicle data and checking.

AP-G34-23 recommends tangential turns for intersection design and gives its
standard turning templates at 5 km/h. OpenArch's waypoint rounding and default
simulation speed follow those settings. See [Austroads AP-G34-23](https://austroads.gov.au/publications/road-design/ap-g34).
