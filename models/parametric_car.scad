$fn = 60;

// Millimetres.
chassis_length = 80;
chassis_width = 40;
chassis_height = 15;
wheel_radius = 10;
wheel_width = 8;
axle_radius = 2;

module car_body() {
    difference() {
        union() {
            cube([chassis_length, chassis_width, chassis_height], center = true);

            translate([-5, 0, 12.5])
                cube([45, 36, 12], center = true);

            translate([20, 0, 8])
                rotate([0, -20, 0])
                    cube([25, 38, 10], center = true);
        }

        for (x = [-22, 22])
            translate([x, 0, -8])
                rotate([90, 0, 0])
                    cylinder(r = 12, h = chassis_width + 5, center = true);

        translate([12, 0, 14])
            rotate([0, -30, 0])
                cube([2, 34, 12], center = true);
    }
}

module wheel() {
    rotate([90, 0, 0]) {
        difference() {
            cylinder(r = wheel_radius, h = wheel_width, center = true);
            cylinder(r = 5, h = wheel_width + 1, center = true);
        }
        cylinder(r = axle_radius, h = wheel_width + 4, center = true);
    }
}

module car_assembly() {
    car_body();

    for (x = [-22, 22])
        for (y = [-21, 21])
            translate([x, y, -8])
                wheel();
}

car_assembly();
