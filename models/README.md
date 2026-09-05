# Apex vertical racer

`apex_racer.scad` is a parametric OpenSCAD approximation of the upright
"vertical racer" concept: a tapered standing fuselage with a forward canopy,
two large ground wheels on a common axle, and two winged wheel pods held out
on struts near the top. Dimensions are in millimetres; the top-level
variables control fuselage size, wheel radii and pod reach.

## Build

```bash
openscad -o apex_racer.stl apex_racer.scad
```

or open the file in OpenSCAD, press **F6**, then export as STL. The model is
roughly 250 mm wide, 60 mm deep and 165 mm tall; the pod fins and struts need
supports when printed upright.
