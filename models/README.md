# Parametric printable car

`parametric_car.scad` is a single-piece assembly based on the supplied car
design. Dimensions are in millimetres; the body and four wheels are generated
from the same source file.

## Build

Open the file in OpenSCAD, press **F6**, then export the rendered result as
`parametric_car.stl`.

The same build can be run headlessly:

```bash
openscad -o parametric_car.stl parametric_car.scad
```

The model is approximately 80 mm long, 52 mm wide including wheels, and 28 mm
high. Print it with the wheels facing sideways as modelled; supports may be
useful under the cabin and hood depending on the slicer's overhang settings.
