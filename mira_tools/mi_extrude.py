# ***** BEGIN GPL LICENSE BLOCK *****
#
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.    See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation,
# Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ***** END GPL LICENCE BLOCK *****

import bpy
import bmesh
import bgl
import blf
import string

from bpy.props import *
from bpy.types import Operator, AddonPreferences

from bpy_extras import view3d_utils

import math
import mathutils as mathu
import random
from mathutils import Vector, Matrix

# if "bpy" in locals():
    # import imp
    # imp.reload(mi_utils_base)
# else:
from . import mi_utils_base as ut_base
from . import mi_color_manager as col_man


class MI_ExtrudeSettings(bpy.types.PropertyGroup):
    # Extrude Settings
    absolute_extrude_step = FloatProperty(default=1.0, min=0.0)
    relative_extrude_step = FloatProperty(default=1.5, min=0.0)
    extrude_step_type = EnumProperty(
        items=(('Asolute', 'Asolute', ''),
               ('Relative', 'Relative', '')
               ),
        default = 'Relative'
    )
    extrude_mode = EnumProperty(
        items=(('Screen', 'Screen', ''),
               ('Raycast', 'Raycast', '')
               ),
        default = 'Screen'
    )

    do_symmetry = BoolProperty(default=False)
    symmetry_axys = EnumProperty(
        items=(('X', 'X', ''),
               ('Y', 'Y', ''),
               ('Z', 'Z', '')
               ),
        default = 'X'
    )


class MI_Extrude_Point():
    # base stuff
    position = None
    direction = None
    verts = None  # indices only
    verts_origins = None  # non deformed position

    # for raycast mode
    hit_normal = None

    # class constructor
    def __init__(self, position, direction, verts, hit_normal):
        self.position, self.direction, self.hit_normal = position, direction, hit_normal
        self.update_verts(verts)

    def update_verts(self, verts):
        self.verts = []
        self.verts_origins = []
        for vert in verts:
            self.verts_origins.append([vert.co[0], vert.co[1], vert.co[2]])
            self.verts.append(vert.index)

    def set_original_position(self, bm):
        bm.verts.ensure_lookup_table()
        for i in range(len(self.verts)):
            bm.verts[self.verts[i]].co[0] = self.verts_origins[i][0]
            bm.verts[self.verts[i]].co[1] = self.verts_origins[i][1]
            bm.verts[self.verts[i]].co[2] = self.verts_origins[i][2]


class MI_StartDraw(bpy.types.Operator):

    """Draw a line with the mouse"""
    bl_idname = "mira.draw_extrude"
    bl_label = "DrawExtrude"
    bl_description = "Draw Extrude Test"
    bl_options = {'REGISTER', 'UNDO'}

    pass_keys = ['NUMPAD_0', 'NUMPAD_1', 'NUMPAD_3', 'NUMPAD_4',
                 'NUMPAD_5', 'NUMPAD_6', 'NUMPAD_7', 'NUMPAD_8',
                 'NUMPAD_9', 'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE',
                 'MOUSEMOVE']
    # 'SELECTMOUSE' 'LEFTMOUSE'

    # extrude_center = None
    # extrude_dir = None

    # curve tool mode
    tool_modes = ('IDLE', 'DRAW', 'ROTATE', 'ROTATE_ALL', 'SCALE', 'SCALE_ALL')
    tool_mode = 'IDLE'

    # changed parameters
    manipulator = None
    relative_step_size = None
    extrude_points = None
    mesh_automerge = None

    # draw on surface settings
    picked_meshes = None
    raycast_offset = None

    # rotate settings
    deform_mouse_pos = None
    scale_all = None
    rotate_all = None

    def invoke(self, context, event):
        if context.area.type == 'VIEW_3D':
            # the arguments we pass the the callbackection
            args = (self, context)
            # Add the region OpenGL drawing callback
            # draw in view space with 'POST_VIEW' and 'PRE_VIEW'

            reset_params(self)

            extrude_settings = context.scene.mi_extrude_settings
            active_obj = context.scene.objects.active
            bm = bmesh.from_edit_mesh(active_obj.data)
            sel_verts = [v for v in bm.verts if v.select]

            if len(sel_verts) == 0:
                self.report({'WARNING'}, "No Selection!!!")
                return {'CANCELLED'}

            else:
                # change parameters
                self.manipulator = context.space_data.show_manipulator
                context.space_data.show_manipulator = False
                self.mesh_automerge = bpy.context.scene.tool_settings.use_mesh_automerge
                bpy.context.scene.tool_settings.use_mesh_automerge = False

                # prepare for snapping
                if extrude_settings.extrude_mode == 'Raycast':
                    sel_objects = [
                        obj for obj in context.selected_objects if obj != active_obj]
                    if sel_objects:
                        self.picked_meshes = ut_base.get_obj_dup_meshes(
                            sel_objects, context)
                    else:
                        self.report(
                            {'WARNING'}, "Please, select objects to raycast!!!")
                        finish_extrude(self, context)
                        return {'CANCELLED'}

                rv3d = context.region_data

                # if we have symmetry
                extrude_center = None
                camera_dir = None
                if extrude_settings.do_symmetry and extrude_settings.extrude_mode == 'Screen':
                    extrude_center = ut_base.get_vertices_center(sel_verts, active_obj, True)
                    if extrude_settings.symmetry_axys == 'X':
                        extrude_center.x = 0.0
                        camera_dir = ut_base.get_obj_axis(active_obj, 'X')
                    if extrude_settings.symmetry_axys == 'Y':
                        extrude_center.y = 0.0
                        camera_dir = ut_base.get_obj_axis(active_obj, 'Y')
                    if extrude_settings.symmetry_axys == 'Z':
                        extrude_center.z = 0.0
                        camera_dir = ut_base.get_obj_axis(active_obj, 'Z')

                    extrude_center = active_obj.matrix_world * extrude_center
                else:
                    extrude_center = ut_base.get_vertices_center(sel_verts, active_obj, False)
                    camera_dir = (rv3d.view_rotation * Vector((0.0, 0.0, -1.0))).normalized()
                    camera_dir.negate()

                # here we create zero extrude point
                new_point = MI_Extrude_Point(
                    extrude_center, None, [], camera_dir)
                self.extrude_points.append(new_point)

                # max_obj_scale
                self.max_obj_scale = active_obj.scale.x
                if active_obj.scale.y > self.max_obj_scale:
                    self.max_obj_scale = active_obj.scale.y
                if active_obj.scale.z > self.max_obj_scale:
                    self.max_obj_scale = active_obj.scale.z

                # relative step
                self.relative_step_size = ut_base.get_vertices_size(
                    sel_verts, active_obj)
                if self.relative_step_size == 0.0 and extrude_settings.extrude_step_type == 'Relative':
                    self.report(
                        {'WARNING'}, "Please, use Absolute step for one point!!!")
                    finish_extrude(self, context)
                    return {'CANCELLED'}

            self.mi_extrude_handle_2d = bpy.types.SpaceView3D.draw_handler_add(
                mi_extrude_draw_2d, args, 'WINDOW', 'POST_PIXEL')
            context.window_manager.modal_handler_add(self)

            return {'RUNNING_MODAL'}
        else:
            self.report({'WARNING'}, "View3D not found, cannot run operator")
            return {'CANCELLED'}


    def modal(self, context, event):
        context.area.tag_redraw()

        context.area.header_text_set("S: Scale, Shift-S: ScaleAll, R: Rotate, Shift-R: RotateAll")

        active_obj = context.scene.objects.active
        bm = bmesh.from_edit_mesh(active_obj.data)

        # check for main keys
        if event.type in {'LEFTMOUSE', 'SELECTMOUSE', 'R', 'S'}:
            if event.value == 'PRESS':
                if self.tool_mode == 'IDLE':
                    m_coords = event.mouse_region_x, event.mouse_region_y
                    if event.type in {'LEFTMOUSE', 'SELECTMOUSE'}:
                        do_pick = mi_pick_extrude_point(
                            self.extrude_points[-1].position, context, m_coords)

                        if do_pick:
                            self.tool_mode = 'DRAW'

                    elif event.type == 'R':
                        self.deform_mouse_pos = m_coords

                        if event.shift is True:
                            self.tool_mode = 'ROTATE_ALL'
                        else:
                            self.tool_mode = 'ROTATE'

                    elif event.type == 'S':
                        self.deform_mouse_pos = m_coords

                        if event.shift is True:
                            self.tool_mode = 'SCALE_ALL'
                        else:
                            self.tool_mode = 'SCALE'

            elif event.value == 'RELEASE':
                if event.type in {'LEFTMOUSE', 'SELECTMOUSE'}:
                    if self.tool_mode == 'ROTATE' or self.tool_mode == 'SCALE':
                        self.extrude_points[-1].update_verts(
                            ut_base.get_selected_bmverts(bm))

                    # update normals after changes
                    if self.tool_mode != 'IDLE':
                        bm.normal_update()

                    self.tool_mode = 'IDLE'

                    return {'RUNNING_MODAL'}

        # logic
        if self.tool_mode == 'DRAW':
            rv3d = context.region_data
            m_coords = event.mouse_region_x, event.mouse_region_y
            extrude_settings = context.scene.mi_extrude_settings

            # get new position according to a mouse
            new_pos = None
            obj_dir_axys = None  # only for symmetry
            best_obj, hit_normal, hit_position = None, None, None

            if extrude_settings.extrude_mode == 'Raycast':
                best_obj, hit_normal, hit_position = ut_base.get_mouse_raycast(
                    context, self.picked_meshes, m_coords, 10000.0)
                new_pos = hit_position

                # set offset for surface normal and extrude_center
                if new_pos is not None:
                    if self.raycast_offset is None:
                        self.raycast_offset = (
                            hit_position - self.extrude_points[-1].position).length
                        new_pos += hit_normal * self.raycast_offset
                    else:
                        new_pos += hit_normal * self.raycast_offset

            else:
                if extrude_settings.do_symmetry and extrude_settings.extrude_mode == 'Screen':
                    if extrude_settings.symmetry_axys == 'X':
                        obj_dir_axys = ut_base.get_obj_axis(active_obj, 'X')
                    if extrude_settings.symmetry_axys == 'Y':
                        obj_dir_axys = ut_base.get_obj_axis(active_obj, 'Y')
                    if extrude_settings.symmetry_axys == 'Z':
                        obj_dir_axys = ut_base.get_obj_axis(active_obj, 'Z')

                    new_pos = ut_base.get_mouse_on_plane(
                        context, self.extrude_points[-1].position, obj_dir_axys, m_coords)
                else:
                    new_pos = ut_base.get_mouse_on_plane(
                        context, self.extrude_points[-1].position, None, m_coords)

            extrude_step = None
            if extrude_settings.extrude_step_type == 'Relative':
                extrude_step = extrude_settings.relative_extrude_step * \
                    self.relative_step_size
            else:
                extrude_step = extrude_settings.absolute_extrude_step

            # EXTRUDE
            if new_pos is not None and (new_pos - self.extrude_points[-1].position).length >= extrude_step:
                # set original position of points
                if self.scale_all != 0.0 or self.rotate_all != 0.0:
                    for extr_point in self.extrude_points:
                        extr_point.set_original_position(bm)

                # disolve edges to fix points indices issues
                if len(self.extrude_points) == 1:
                    bpy.ops.mesh.dissolve_faces()

                # main extrude things
                bpy.ops.mesh.extrude_region_move()
                selected_bmesh = ut_base.get_selected_bmesh(bm)

                # New Extrude center
                offset_dir = None
                offset_move = new_pos - self.extrude_points[-1].position
                bpy.ops.transform.translate(
                    value=(offset_move.x, offset_move.y, offset_move.z), proportional='DISABLED')

                offset_dir = offset_move.copy().normalized()
                up_vec = None
                cam_dir = None

                # if we have symmetry
                if extrude_settings.do_symmetry and extrude_settings.extrude_mode == 'Screen':
                    cam_dir = obj_dir_axys
                else:
                    cam_dir = (rv3d.view_rotation * Vector((0.0, 0.0, -1.0))).normalized()

                # rotate if we have 2 extrude points at least
                rotate_dir_vec = None
                if len(self.extrude_points) > 1:
                    # ratate direction
                    rot_angle = self.extrude_points[
                        -1].direction.angle(offset_dir)

                    if extrude_settings.extrude_mode == 'Raycast':
                        rotate_dir_vec = self.extrude_points[
                            -1].direction.cross(offset_dir)
                    else:
                        rotate_dir_vec = cam_dir

                    # Possibly this does not need for Raycast
                    up_vec = rotate_dir_vec.cross(
                        self.extrude_points[-1].direction).normalized()
                    if up_vec.angle(offset_dir) > math.radians(90):
                        rot_angle = -rot_angle

                    ## Direction rotate
                    #bpy.ops.transform.rotate(
                        #value=rot_angle, axis=rotate_dir_vec, proportional='DISABLED')

                    # rotate verts!
                    rot_mat = Matrix.Rotation(rot_angle, 3,  rotate_dir_vec)
                    for vert in selected_bmesh[0]:
                        vert.co = active_obj.matrix_world.inverted() * (rot_mat * ((active_obj.matrix_world * vert.co) - new_pos) + new_pos)

                    self.extrude_points[-1].direction = offset_dir
                else:
                    # fix first extrude
                    if extrude_settings.extrude_mode == 'Raycast':
                        fix_first_extrude_dir = ut_base.get_mouse_on_plane(
                            context, self.extrude_points[-1].position, None, m_coords)
                        self.extrude_points[-1].direction = (
                            fix_first_extrude_dir - self.extrude_points[-1].position).normalized()
                    else:
                        self.extrude_points[-1].direction = offset_dir

                # finalize things
                # empty array will be for extruded vertices
                # hit_normal is only for raycast mode
                new_point = MI_Extrude_Point(new_pos, self.extrude_points[
                                             -1].direction, ut_base.get_selected_bmverts(bm), hit_normal)
                self.extrude_points.append(new_point)
                # self.extrude_points[-1].position = new_pos

                # fix direction of previous step
                if len(self.extrude_points) > 2:
                    fix_step = self.extrude_points[-2]
                    fix_dir = (
                        self.extrude_points[-1].position - self.extrude_points[-3].position).normalized()
                    fix_up_vec = rotate_dir_vec.cross(fix_dir).normalized()
                    fix_rot_angle = fix_dir.angle(fix_step.direction)

                    previous_extrude_verts = get_previous_extrude_verts(
                        bm, context)

                    # rotate previous extrude
                    if fix_rot_angle > 0.0:
                        for vert in selected_bmesh[0]:
                            vert.select = False
                        for edge in selected_bmesh[1]:
                            edge.select = False
                        for face in selected_bmesh[2]:
                            face.select = False

                        # rotate previous extrude to fix rotation
                        if fix_up_vec.angle((fix_step.direction - fix_dir).normalized()) > math.radians(90):
                            fix_rot_angle = -fix_rot_angle

                        # rotate verts!
                        rot_mat = Matrix.Rotation(fix_rot_angle, 3,  rotate_dir_vec)
                        for vert in previous_extrude_verts:
                            vert.co = active_obj.matrix_world.inverted() * (rot_mat * ((active_obj.matrix_world * vert.co) - fix_step.position) + fix_step.position)
                            vert.select = False

                        for vert in selected_bmesh[0]:
                            vert.select = True
                        for edge in selected_bmesh[1]:
                            edge.select = True
                        for face in selected_bmesh[2]:
                            face.select = True

                    # chenge direction of previous extrude
                    fix_step.direction = fix_dir
                    # add verts of previos extrude
                    fix_step.update_verts(previous_extrude_verts)

                    # apply scale and rotation
                    if self.scale_all != 0.0:
                        scale_all_epoints(active_obj, bm, self.extrude_points, self.scale_all)
                    if self.rotate_all != 0.0:
                        rotate_all_epoints(
                            active_obj, bm, self.extrude_points, self.rotate_all)

                #bm.normal_update()
                bmesh.update_edit_mesh(active_obj.data)

            return {'RUNNING_MODAL'}

        elif len(self.extrude_points) > 0:
            # rotate/scale code
            if self.tool_mode in {'ROTATE', 'ROTATE_ALL', 'SCALE', 'SCALE_ALL'}:
                m_coords = event.mouse_region_x, event.mouse_region_y

                # set original position
                if self.tool_mode in {'SCALE', 'ROTATE'}:
                    self.extrude_points[-1].set_original_position(bm)
                else:
                    for extr_point in self.extrude_points:
                        extr_point.set_original_position(bm)

                # main stuff
                if self.tool_mode in {'SCALE', 'SCALE_ALL'}:
                    # scale epoint
                    if self.tool_mode is 'SCALE':
                        new_scale = (
                            m_coords[0] - self.deform_mouse_pos[0]) * 0.01
                        scale_epoint(active_obj, bm, self.extrude_points[-1], new_scale)

                    # scale all
                    else:

                        points_size = len(self.extrude_points)
                        self.scale_all += math.radians(
                            (m_coords[0] - self.deform_mouse_pos[0]) * 0.01 * points_size)
                        scale_all_epoints(active_obj, bm, self.extrude_points, self.scale_all)

                        if self.rotate_all != 0.0:
                            rotate_all_epoints(
                                active_obj, bm, self.extrude_points, self.rotate_all)

                        self.deform_mouse_pos = m_coords

                elif self.tool_mode in {'ROTATE', 'ROTATE_ALL'}:
                    # rotate epoint
                    if self.tool_mode is 'ROTATE':
                        new_rot_angle = math.radians(
                            (m_coords[0] - self.deform_mouse_pos[0]) * 0.3)
                        rotate_epoint(
                            active_obj, bm, self.extrude_points[-1], new_rot_angle)

                    # rotate all
                    else:
                        if self.scale_all != 0.0:
                            scale_all_epoints(active_obj, bm, self.extrude_points, self.scale_all)

                        points_size = len(self.extrude_points)
                        self.rotate_all += math.radians(
                            (m_coords[0] - self.deform_mouse_pos[0]) * 0.3 * points_size)
                        rotate_all_epoints(
                            active_obj, bm, self.extrude_points, self.rotate_all)

                        self.deform_mouse_pos = m_coords

                #bm.normal_update()
                bmesh.update_edit_mesh(active_obj.data)

                return {'RUNNING_MODAL'}

        # main stuff
        if event.type in {'RIGHTMOUSE', 'ESC'}:
            finish_extrude(self, context)
            # bpy.types.SpaceView3D.draw_handler_remove(self.mi_handle_3d,
            # 'WINDOW')
            bpy.types.SpaceView3D.draw_handler_remove(
                self.mi_extrude_handle_2d, 'WINDOW')
            context.area.header_text_set()

            return {'FINISHED'}

        elif event.type in self.pass_keys:
            # allow navigation
            return {'PASS_THROUGH'}

        return {'RUNNING_MODAL'}
        # return {'PASS_THROUGH'}


def reset_params(self):
    # self.extrude_center = None
    # self.extrude_dir = None
    self.tool_mode = 'IDLE'
    self.relative_step_size = None
    self.extrude_points = []
    self.picked_meshes = None
    self.raycast_offset = None

    # deform settings
    self.deform_mouse_pos = None
    self.scale_all = 0.0
    self.rotate_all = 0.0


def finish_extrude(self, context):
    context.space_data.show_manipulator = self.manipulator
    bpy.context.scene.tool_settings.use_mesh_automerge = self.mesh_automerge
    self.extrude_points = None


def mi_extrude_draw_2d(self, context):
    active_obj = context.scene.objects.active
    region = context.region
    rv3d = context.region_data
    point_pos_2d = view3d_utils.location_3d_to_region_2d(
        region, rv3d, self.extrude_points[-1].position)

    p_col = col_man.dre_point_base
    mi_draw_2d_point(point_pos_2d.x, point_pos_2d.y, 6, p_col)


def get_previous_extrude_verts(bm, context):
    verts_array = None
    verts1 = ut_base.get_selected_bmverts_ids(bm)
    bpy.ops.mesh.select_more()

    verts2 = ut_base.get_selected_bmverts_ids(bm)
    bpy.ops.mesh.select_less()

    new_verts = []
    bm.verts.ensure_lookup_table()
    for vert in verts2:
        if vert not in verts1:
            new_verts.append(bm.verts[vert])

    return new_verts


# TODO move to utils
def mi_pick_extrude_point(point, context, mouse_coords):
    region = context.region
    rv3d = context.region_data

    # for cu_point in curve.curve_points:
    point_pos_2d = view3d_utils.location_3d_to_region_2d(region, rv3d, point)
    length = (point_pos_2d - Vector(mouse_coords)).length
    if length <= 9.0:
        return True

    return False


# TODO MOVE TO UTILITIES
def mi_draw_2d_point(point_x, point_y, p_size=4, p_col=(1.0, 1.0, 1.0, 1.0)):
    bgl.glEnable(bgl.GL_BLEND)
    # bgl.glColor4f(1.0, 1.0, 1.0, 0.5)
    # bgl.glLineWidth(2)

    bgl.glPointSize(p_size)
#    bgl.glBegin(bgl.GL_LINE_LOOP)
    bgl.glBegin(bgl.GL_POINTS)
 #   bgl.glBegin(bgl.GL_POLYGON)
    bgl.glColor4f(p_col[0], p_col[1], p_col[2], p_col[3])
    bgl.glVertex2f(point_x, point_y)
    bgl.glEnd()

    # restore opengl defaults
    bgl.glLineWidth(1)
    bgl.glDisable(bgl.GL_BLEND)
    bgl.glColor4f(0.0, 0.0, 0.0, 1.0)


def rotate_verts(verts, rot_angle, axis, rot_origin):
    for vert in verts:
        rot_mat = Matrix.Rotation(rot_angle, 3, axis)
        vert.co = rot_mat * (vert.co - rot_origin) + rot_origin


def scale_verts(verts, scale_value, origin):
    for vert in verts:
        vert.co += (vert.co - origin) * scale_value


def scale_epoint(obj, bm, epoint, scale_value):
    deform_center = obj.matrix_world.inverted() * epoint.position
    the_verts = ut_base.get_bmverts_from_ids(bm, epoint.verts)
    scale_verts(the_verts, scale_value, deform_center)


def scale_all_epoints(obj, bm, epoints, scale_value):
    points_size = len(epoints)
    for i in range(points_size):
        deform_center = obj.matrix_world.inverted() * epoints[i].position
        new_scale = scale_value * (float(i) / float(points_size))
        # new_scale = sorted((0.0, new_scale, scale_value))[1]
        the_verts = ut_base.get_bmverts_from_ids(bm, epoints[i].verts)
        scale_verts(the_verts, new_scale, deform_center)


def rotate_epoint(obj, bm, epoint, rot_angle):
    deform_center = obj.matrix_world.inverted() * epoint.position
    deform_dir = (epoint.direction * obj.matrix_world).normalized()
    # deform_dir = obj.matrix_world.inverted().to_quaternion() * epoint.direction
    the_verts = ut_base.get_bmverts_from_ids(bm, epoint.verts)
    rotate_verts(the_verts, rot_angle, deform_dir, deform_center)


def rotate_all_epoints(obj, bm, epoints, rotate_value):
    points_size = len(epoints)
    for i in range(points_size):
        deform_center = obj.matrix_world.inverted() * epoints[i].position
        deform_dir = (epoints[i].direction * obj.matrix_world).normalized()
        # deform_dir = obj.matrix_world.inverted().to_quaternion() * epoints[i].direction
        new_rot_angle = rotate_value * (float(i) / float(points_size))
        the_verts = ut_base.get_bmverts_from_ids(bm, epoints[i].verts)
        rotate_verts(the_verts, new_rot_angle, deform_dir, deform_center)
