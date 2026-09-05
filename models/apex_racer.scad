$fn = 72;

// Millimetres. Upright "vertical racer": a standing fuselage on two ground
// wheels, with two winged wheel pods held out on struts near the top.
fuselage_height = 160;
fuselage_width = 34;
fuselage_depth = 40;
canopy_height = 60;

ground_wheel_radius = 24;
ground_wheel_width = 22;
ground_track = 62;

pod_height = 118;
pod_reach = 70;
pod_wheel_radius = 18;
pod_wheel_width = 14;

module fuselage() {
    hull() {
        translate([0, 0, 6])
            cube([fuselage_width + 18, fuselage_depth + 6, 12], center = true);
        translate([0, 0, fuselage_height * 0.55])
            scale([fuselage_width / 2, fuselage_depth / 2, 1])
                cylinder(r = 1, h = 1, center = true);
        translate([0, 0, fuselage_height])
            scale([fuselage_width / 4, fuselage_depth / 4, 1])
                sphere(r = 1);
    }
}

module canopy() {
    z0 = fuselage_height - canopy_height - 24;
    translate([0, -fuselage_depth * 0.42, z0])
        hull() {
            scale([fuselage_width * 0.36, fuselage_depth * 0.3, 1])
                cylinder(r = 1, h = 1);
            translate([0, 0, canopy_height])
                sphere(r = 4);
        }
}

module side_plate(w, h, t = 3) {
    cube([w, t, h], center = true);
}

module wheel(r, w, hub = 0.45) {
    rotate([90, 0, 0]) {
        difference() {
            cylinder(r = r, h = w, center = true);
            cylinder(r = r * hub, h = w + 1, center = true);
        }
        cylinder(r = r * hub * 0.5, h = w + 4, center = true);
        for (i = [0 : 4])
            rotate([0, 0, i * 72])
                translate([r * hub * 0.5, 0, 0])
                    cube([r * hub, 3, w * 0.8], center = true);
    }
}

module fin(l, w, t = 2.5) {
    hull() {
        cube([t, w, 4], center = true);
        translate([0, w * 0.15, l])
            cube([t, w * 0.35, 2], center = true);
    }
}

module wheel_pod(mirror_x = 1) {
    translate([mirror_x * (fuselage_width / 2 + pod_reach), 0, pod_height]) {
        rotate([0, 0, 90]) wheel(pod_wheel_radius, pod_wheel_width);
        rotate([0, 90, 0])
            cylinder(r = pod_wheel_radius * 0.5, h = pod_wheel_width + 16, center = true);
        // radiating fins on the outboard face
        for (a = [-40, 0, 40, 180])
            translate([mirror_x * (pod_wheel_width / 2 + 6), 0, 0])
                rotate([a, 0, 0])
                    translate([0, 0, pod_wheel_radius * 0.6])
                        rotate([0, 0, 90])
                            fin(pod_wheel_radius * 1.8, 14);
        // vertical sponsor plate hanging below the pod
        translate([-mirror_x * 18, 0, -pod_wheel_radius - 22])
            side_plate(22, 40);
    }
    // strut from the fuselage to the pod
    hull() {
        translate([mirror_x * fuselage_width / 2, 0, pod_height - 6])
            cube([4, 16, 14], center = true);
        translate([mirror_x * (fuselage_width / 2 + pod_reach - pod_wheel_radius * 0.5), 0, pod_height])
            cube([4, 12, 10], center = true);
    }
    // upper sponsor plate
    translate([mirror_x * (fuselage_width / 2 + pod_reach * 0.55), 0, pod_height + 28])
        side_plate(28, 30);
}

module ground_wheels() {
    for (x = [-1, 1])
        translate([x * ground_track / 2, 0, ground_wheel_radius])
            rotate([0, 0, 90])
                wheel(ground_wheel_radius, ground_wheel_width, 0.55);
    // axle + front splitter
    translate([0, 0, ground_wheel_radius])
        rotate([0, 90, 0])
            cylinder(r = 3, h = ground_track, center = true);
    translate([0, -fuselage_depth * 0.7, 4])
        cube([fuselage_width + 30, 10, 6], center = true);
}

module apex_racer() {
    translate([0, 0, 2]) fuselage();
    canopy();
    ground_wheels();
    for (m = [-1, 1]) wheel_pod(m);
    // tail fins on the fuselage flanks
    for (m = [-1, 1])
        translate([m * (fuselage_width / 2 + 6), 0, 45])
            side_plate(14, 40, 3);
}

apex_racer();
