"""
Container for the layout.
(Containers can contain other containers or user interface controls.)
"""
from __future__ import unicode_literals

from abc import ABCMeta, abstractmethod
from six import with_metaclass, text_type
from six.moves import range

from .controls import UIControl, FormattedTextControl, UIContent, DummyControl
from .dimension import Dimension, sum_layout_dimensions, max_layout_dimensions, to_dimension, is_dimension
from .margins import Margin
from .screen import Point, WritePosition, _CHAR_CACHE
from .utils import fragment_list_to_text, explode_text_fragments, fragment_list_width
from prompt_toolkit.application.current import get_app
from prompt_toolkit.cache import SimpleCache
from prompt_toolkit.filters import to_filter, ViInsertMode, EmacsInsertMode
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from prompt_toolkit.reactive import Integer
from prompt_toolkit.utils import take_using_weights, get_cwidth

__all__ = (
    'Container',
    'HSplit',
    'VSplit',
    'FloatContainer',
    'Float',
    'Window',
    'WindowRenderInfo',
    'ConditionalContainer',
    'ScrollOffsets',
    'ColorColumn',
    'to_container',
    'to_window',
    'is_container',
)


class Container(with_metaclass(ABCMeta, object)):
    """
    Base class for user interface layout.
    """
    @abstractmethod
    def reset(self):
        """
        Reset the state of this container and all the children.
        (E.g. reset scroll offsets, etc...)
        """

    @abstractmethod
    def preferred_width(self, max_available_width):
        """
        Return a :class:`~prompt_toolkit.layout.dimension.Dimension` that
        represents the desired width for this container.
        """

    @abstractmethod
    def preferred_height(self, width, max_available_height):
        """
        Return a :class:`~prompt_toolkit.layout.dimension.Dimension` that
        represents the desired height for this container.
        """

    @abstractmethod
    def write_to_screen(self, screen, mouse_handlers, write_position, parent_style, erase_bg):
        """
        Write the actual content to the screen.

        :param screen: :class:`~prompt_toolkit.layout.screen.Screen`
        :param mouse_handlers: :class:`~prompt_toolkit.layout.mouse_handlers.MouseHandlers`.
        :param parent_style: Style string to pass to the `Window` object. This will be
            applied to all content of the windows. `VSplit` and `HSplit` can
            use it to pass their style down to the windows that they contain.
        """

    def is_modal(self):
        """
        When this container is modal, key bindings from parent containers are
        not taken into account if a user control in this container is focussed.
        """
        return False

    def get_key_bindings(self):
        """
        Returns a `KeyBindings` object. These bindings become active when any
        user control in this container has the focus, except if any containers
        between this container and the focussed user control is modal.
        """
        return None

    @abstractmethod
    def get_children(self):
        """
        Return the list of child :class:`.Container` objects.
        """
        return []


def _window_too_small():
    " Create a `Window` that displays the 'Window too small' text. "
    return Window(FormattedTextControl(text=
        [('class:window-too-small', ' Window too small... ')]))


class VerticalAlign:
    " Alignment for `HSplit`. "
    TOP = 'TOP'
    CENTER = 'CENTER'
    BOTTOM = 'BOTTOM'
    JUSTIFY = 'JUSTIFY'


class HorizontalAlign:
    " Alignment for `VSplit`. "
    LEFT = 'LEFT'
    CENTER = 'CENTER'
    RIGHT = 'RIGHT'
    JUSTIFY = 'JUSTIFY'


class _Split(Container):
    """
    The common parts of `VSplit` and `HSplit`.
    """
    def __init__(self, children, window_too_small=None, padding=Dimension.exact(0),
                 width=None, height=None, modal=False, key_bindings=None, style=''):
        assert window_too_small is None or isinstance(window_too_small, Container)
        assert isinstance(children, list)
        assert isinstance(modal, bool)
        assert isinstance(style, text_type)
        assert is_dimension(width)
        assert is_dimension(height)
        assert is_dimension(padding)

        self.children = [to_container(c) for c in children]
        self.window_too_small = window_too_small or _window_too_small()
        self.padding = to_dimension(padding)

        self.width = width
        self.height = height

        self.modal = modal
        self.key_bindings = key_bindings
        self.style = style

    def is_modal(self):
        return self.modal

    def get_key_bindings(self):
        return self.key_bindings

    def get_children(self):
        return self.children


class HSplit(_Split):
    """
    Several layouts, one stacked above/under the other. ::

        +--------------------+
        |                    |
        +--------------------+
        |                    |
        +--------------------+

    :param children: List of child :class:`.Container` objects.
    :param window_too_small: A :class:`.Container` object that is displayed if
        there is not enough space for all the children. By default, this is a
        "Window too small" message.
    :param width: When given, use this width instead of looking at the children.
    :param height: When given, use this width instead of looking at the children.
    :param style: A style string.
    :param modal: ``True`` or ``False``.
    :param key_bindings: ``None`` or a ``KeyBindings`` object.
    """
    def __init__(self, children, window_too_small=None,
                 align=VerticalAlign.JUSTIFY, padding=0, width=None,
                 height=None, modal=False, key_bindings=None, style=''):
        super(HSplit, self).__init__(
            children=children, window_too_small=window_too_small,
            padding=padding, width=width, height=height, modal=modal,
            key_bindings=key_bindings, style=style)

        self.align = align

        self._children_cache = SimpleCache(maxsize=1)
        self._remaining_space_window = Window()  # Dummy window.

    def preferred_width(self, max_available_width):
        if self.width is not None:
            return to_dimension(self.width)

        if self.children:
            dimensions = [c.preferred_width(max_available_width)
                          for c in self.children]
            return max_layout_dimensions(dimensions)
        else:
            return Dimension()

    def preferred_height(self, width, max_available_height):
        if self.height is not None:
            return to_dimension(self.height)

        dimensions = [c.preferred_height(width, max_available_height)
                      for c in self._all_children]
        return sum_layout_dimensions(dimensions)

    def reset(self):
        for c in self.children:
            c.reset()

    @property
    def _all_children(self):
        """
        List of child objects, including padding.
        """
        def get():
            result = []

            # Padding Top.
            if self.align in (VerticalAlign.CENTER, VerticalAlign.BOTTOM):
                result.append(Window(width=Dimension(preferred=0)))

            # The children with padding.
            for child in self.children:
                result.append(child)
                result.append(Window(height=self.padding))
            result.pop()

            # Padding right.
            if self.align in (VerticalAlign.CENTER, VerticalAlign.TOP):
                result.append(Window(width=Dimension(preferred=0)))

            return result

        return self._children_cache.get(tuple(self.children), get)

    def write_to_screen(self, screen, mouse_handlers, write_position, parent_style, erase_bg):
        """
        Render the prompt to a `Screen` instance.

        :param screen: The :class:`~prompt_toolkit.layout.screen.Screen` class
            to which the output has to be written.
        """
        sizes = self._divide_heigths(write_position)
        style = parent_style + ' ' + self.style

        if sizes is None:
            self.window_too_small.write_to_screen(
                screen, mouse_handlers, write_position, style, erase_bg)
        else:
            #
            ypos = write_position.ypos
            xpos = write_position.xpos
            width = write_position.width

            # Draw child panes.
            for s, c in zip(sizes, self._all_children):
                c.write_to_screen(screen, mouse_handlers,
                                  WritePosition(xpos, ypos, width, s), style, erase_bg)
                ypos += s

            # Fill in the remaining space. This happens when a child control
            # refuses to take more space and we don't have any padding. Adding a
            # dummy child control for this (in `self._all_children`) is not
            # desired, because in some situations, it would take more space, even
            # when it's not required. This is required to apply the styling.
            remaining_height = write_position.ypos + write_position.height - ypos
            if remaining_height > 0:
                self._remaining_space_window.write_to_screen(
                    screen, mouse_handlers,
                    WritePosition(xpos, ypos, width, remaining_height), style, erase_bg)

    def _divide_heigths(self, write_position):
        """
        Return the heights for all rows.
        Or None when there is not enough space.
        """
        if not self.children:
            return []

        width = write_position.width
        height = write_position.height

        # Calculate heights.
        dimensions = [
            c.preferred_height(width, height)
            for c in self._all_children]

        # Sum dimensions
        sum_dimensions = sum_layout_dimensions(dimensions)

        # If there is not enough space for both.
        # Don't do anything.
        if sum_dimensions.min > height:
            return

        # Find optimal sizes. (Start with minimal size, increase until we cover
        # the whole height.)
        sizes = [d.min for d in dimensions]

        child_generator = take_using_weights(
            items=list(range(len(dimensions))),
            weights=[d.weight for d in dimensions])

        i = next(child_generator)

        # Increase until we meet at least the 'preferred' size.
        preferred_stop = min(height, sum_dimensions.preferred)
        preferred_dimensions = [d.preferred for d in dimensions]

        while sum(sizes) < preferred_stop:
            if sizes[i] < preferred_dimensions[i]:
                sizes[i] += 1
            i = next(child_generator)

        # Increase until we use all the available space. (or until "max")
        if not get_app().is_done:
            max_stop = min(height, sum_dimensions.max)
            max_dimensions = [d.max for d in dimensions]

            while sum(sizes) < max_stop:
                if sizes[i] < max_dimensions[i]:
                    sizes[i] += 1
                i = next(child_generator)

        return sizes


class VSplit(_Split):
    """
    Several layouts, one stacked left/right of the other. ::

        +---------+----------+
        |         |          |
        |         |          |
        +---------+----------+

    :param children: List of child :class:`.Container` objects.
    :param window_too_small: A :class:`.Container` object that is displayed if
        there is not enough space for all the children. By default, this is a
        "Window too small" message.
    :param width: When given, use this width instead of looking at the children.
    :param height: When given, use this width instead of looking at the children.
    :param style: A style string.
    :param modal: ``True`` or ``False``.
    :param key_bindings: ``None`` or a ``KeyBindings`` object.
    """
    def __init__(self, children, window_too_small=None, align=HorizontalAlign.JUSTIFY,
                 padding=Dimension.exact(0), width=None, height=None,
                 modal=False, key_bindings=None, style=''):
        super(VSplit, self).__init__(
            children=children, window_too_small=window_too_small,
            padding=padding, width=width, height=height, modal=modal,
            key_bindings=key_bindings, style=style)

        self.align = align

        self._children_cache = SimpleCache(maxsize=1)
        self._remaining_space_window = Window()  # Dummy window.

    def preferred_width(self, max_available_width):
        if self.width is not None:
            return to_dimension(self.width)

        dimensions = [c.preferred_width(max_available_width)
                      for c in self._all_children]

        return sum_layout_dimensions(dimensions)

    def preferred_height(self, width, max_available_height):
        if self.height is not None:
            return to_dimension(self.height)

        # At the point where we want to calculate the heights, the widths have
        # already been decided. So we can trust `width` to be the actual
        # `width` that's going to be used for the rendering. So,
        # `divide_widths` is supposed to use all of the available width.
        # Using only the `preferred` width caused a bug where the reported
        # height was more than required. (we had a `BufferControl` which did
        # wrap lines because of the smaller width returned by `_divide_widths`.

        sizes = self._divide_widths(width)
        children = self._all_children

        if sizes is None:
            return Dimension()
        else:
            dimensions = [c.preferred_height(s, max_available_height)
                          for s, c in zip(sizes, children)]
            return max_layout_dimensions(dimensions)

    def reset(self):
        for c in self.children:
            c.reset()

    @property
    def _all_children(self):
        """
        List of child objects, including padding.
        """
        def get():
            result = []

            # Padding left.
            if self.align in (HorizontalAlign.CENTER, HorizontalAlign.RIGHT):
                result.append(Window(width=Dimension(preferred=0)))

            # The children with padding.
            for child in self.children:
                result.append(child)
                result.append(Window(width=self.padding))
            result.pop()

            # Padding right.
            if self.align in (HorizontalAlign.CENTER, HorizontalAlign.LEFT):
                result.append(Window(width=Dimension(preferred=0)))

            return result

        return self._children_cache.get(tuple(self.children), get)

    def _divide_widths(self, width):
        """
        Return the widths for all columns.
        Or None when there is not enough space.
        """
        children = self._all_children

        if not children:
            return []

        # Calculate widths.
        dimensions = [c.preferred_width(width) for c in children]
        preferred_dimensions = [d.preferred for d in dimensions]

        # Sum dimensions
        sum_dimensions = sum_layout_dimensions(dimensions)

        # If there is not enough space for both.
        # Don't do anything.
        if sum_dimensions.min > width:
            return

        # Find optimal sizes. (Start with minimal size, increase until we cover
        # the whole width.)
        sizes = [d.min for d in dimensions]

        child_generator = take_using_weights(
            items=list(range(len(dimensions))),
            weights=[d.weight for d in dimensions])

        i = next(child_generator)

        # Increase until we meet at least the 'preferred' size.
        preferred_stop = min(width, sum_dimensions.preferred)

        while sum(sizes) < preferred_stop:
            if sizes[i] < preferred_dimensions[i]:
                sizes[i] += 1
            i = next(child_generator)

        # Increase until we use all the available space.
        max_dimensions = [d.max for d in dimensions]
        max_stop = min(width, sum_dimensions.max)

        while sum(sizes) < max_stop:
            if sizes[i] < max_dimensions[i]:
                sizes[i] += 1
            i = next(child_generator)

        return sizes

    def write_to_screen(self, screen, mouse_handlers, write_position, parent_style, erase_bg):
        """
        Render the prompt to a `Screen` instance.

        :param screen: The :class:`~prompt_toolkit.layout.screen.Screen` class
            to which the output has to be written.
        """
        if not self.children:
            return

        children = self._all_children
        sizes = self._divide_widths(write_position.width)
        style = parent_style + ' ' + self.style

        # If there is not enough space.
        if sizes is None:
            self.window_too_small.write_to_screen(
                screen, mouse_handlers, write_position, style, erase_bg)
            return

        # Calculate heights, take the largest possible, but not larger than
        # write_position.height.
        heights = [child.preferred_height(width, write_position.height).preferred
                   for width, child in zip(sizes, children)]
        height = max(write_position.height, min(write_position.height, max(heights)))

        #
        ypos = write_position.ypos
        xpos = write_position.xpos

        # Draw all child panes.
        for s, c in zip(sizes, children):
            c.write_to_screen(screen, mouse_handlers,
                              WritePosition(xpos, ypos, s, height), style, erase_bg)
            xpos += s

        # Fill in the remaining space. This happens when a child control
        # refuses to take more space and we don't have any padding. Adding a
        # dummy child control for this (in `self._all_children`) is not
        # desired, because in some situations, it would take more space, even
        # when it's not required. This is required to apply the styling.
        remaining_width = write_position.xpos + write_position.width - xpos
        if remaining_width > 0:
            self._remaining_space_window.write_to_screen(
                screen, mouse_handlers,
                WritePosition(xpos, ypos, remaining_width, height), style, erase_bg)


class FloatContainer(Container):
    """
    Container which can contain another container for the background, as well
    as a list of floating containers on top of it.

    Example Usage::

        FloatContainer(content=Window(...),
                       floats=[
                           Float(xcursor=True,
                                ycursor=True,
                                layout=CompletionMenu(...))
                       ])
    """
    def __init__(self, content, floats, modal=False, key_bindings=None, style=''):
        assert all(isinstance(f, Float) for f in floats)
        assert isinstance(modal, bool)
        assert isinstance(style, text_type)

        self.content = to_container(content)
        self.floats = floats

        self.modal = modal
        self.key_bindings = key_bindings
        self.style = style

    def reset(self):
        self.content.reset()

        for f in self.floats:
            f.content.reset()

    def preferred_width(self, write_position):
        return self.content.preferred_width(write_position)

    def preferred_height(self, width, max_available_height):
        """
        Return the preferred height of the float container.
        (We don't care about the height of the floats, they should always fit
        into the dimensions provided by the container.)
        """
        return self.content.preferred_height(width, max_available_height)

    def write_to_screen(self, screen, mouse_handlers, write_position, parent_style, erase_bg):
        style = parent_style + ' ' + self.style

        self.content.write_to_screen(screen, mouse_handlers, write_position, style, erase_bg)

        for fl in self.floats:
            # When a menu_position was given, use this instead of the cursor
            # position. (These cursor positions are absolute, translate again
            # relative to the write_position.)
            # Note: This should be inside the for-loop, because one float could
            #       set the cursor position to be used for the next one.
            cpos = screen.get_menu_position(fl.attach_to_window or get_app().layout.current_window)
            cursor_position = Point(x=cpos.x - write_position.xpos,
                                    y=cpos.y - write_position.ypos)

            fl_width = fl.get_width()
            fl_height = fl.get_height()

            # Left & width given.
            if fl.left is not None and fl_width is not None:
                xpos = fl.left
                width = fl_width
            # Left & right given -> calculate width.
            elif fl.left is not None and fl.right is not None:
                xpos = fl.left
                width = write_position.width - fl.left - fl.right
            # Width & right given -> calculate left.
            elif fl_width is not None and fl.right is not None:
                xpos = write_position.width - fl.right - fl_width
                width = fl_width
            elif fl.xcursor:
                width = fl_width
                if width is None:
                    width = fl.content.preferred_width(write_position.width).preferred
                    width = min(write_position.width, width)

                xpos = cursor_position.x
                if xpos + width > write_position.width:
                    xpos = max(0, write_position.width - width)
            # Only width given -> center horizontally.
            elif fl_width:
                xpos = int((write_position.width - fl_width) / 2)
                width = fl_width
            # Otherwise, take preferred width from float content.
            else:
                width = fl.content.preferred_width(write_position.width).preferred

                if fl.left is not None:
                    xpos = fl.left
                elif fl.right is not None:
                    xpos = max(0, write_position.width - width - fl.right)
                else:  # Center horizontally.
                    xpos = max(0, int((write_position.width - width) / 2))

                # Trim.
                width = min(width, write_position.width - xpos)

            # Top & height given.
            if fl.top is not None and fl_height is not None:
                ypos = fl.top
                height = fl_height
            # Top & bottom given -> calculate height.
            elif fl.top is not None and fl.bottom is not None:
                ypos = fl.top
                height = write_position.height - fl.top - fl.bottom
            # Height & bottom given -> calculate top.
            elif fl_height is not None and fl.bottom is not None:
                ypos = write_position.height - fl_height - fl.bottom
                height = fl_height
            # Near cursor.
            elif fl.ycursor:
                ypos = cursor_position.y + (0 if fl.allow_cover_cursor else 1)

                height = fl_height
                if height is None:
                    height = fl.content.preferred_height(
                        width, write_position.height).preferred

                # Reduce height if not enough space. (We can use the height
                # when the content requires it.)
                if height > write_position.height - ypos:
                    if write_position.height - ypos + 1 >= ypos:
                        # When the space below the cursor is more than
                        # the space above, just reduce the height.
                        height = write_position.height - ypos
                    else:
                        # Otherwise, fit the float above the cursor.
                        height = min(height, cursor_position.y)
                        ypos = cursor_position.y - height

            # Only height given -> center vertically.
            elif fl_width:
                ypos = int((write_position.height - fl_height) / 2)
                height = fl_height
            # Otherwise, take preferred height from content.
            else:
                height = fl.content.preferred_height(
                    width, write_position.height).preferred

                if fl.top is not None:
                    ypos = fl.top
                elif fl.bottom is not None:
                    ypos = max(0, write_position.height - height - fl.bottom)
                else:  # Center vertically.
                    ypos = max(0, int((write_position.height - height) / 2))

                # Trim.
                height = min(height, write_position.height - ypos)

            # Write float.
            # (xpos and ypos can be negative: a float can be partially visible.)
            if height > 0 and width > 0:
                wp = WritePosition(xpos=xpos + write_position.xpos,
                                   ypos=ypos + write_position.ypos,
                                   width=width, height=height)

                if not fl.hide_when_covering_content or self._area_is_empty(screen, wp):
                    fl.content.write_to_screen(screen, mouse_handlers, wp, style, erase_bg=True)

    def _area_is_empty(self, screen, write_position):
        """
        Return True when the area below the write position is still empty.
        (For floats that should not hide content underneath.)
        """
        wp = write_position

        for y in range(wp.ypos, wp.ypos + wp.height):
            if y in screen.data_buffer:
                row = screen.data_buffer[y]

                for x in range(wp.xpos, wp.xpos + wp.width):
                    c = row[x]
                    if c.char != ' ':
                        return False

        return True

    def is_modal(self):
        return self.modal

    def get_key_bindings(self):
        return self.key_bindings

    def get_children(self):
        children = [self.content]
        children.extend(f.content for f in self.floats)
        return children


class Float(object):
    """
    Float for use in a :class:`.FloatContainer`.
    Except for the `content` parameter, all other options are optional.

    :param content: :class:`.Container` instance.

    :param width: `Dimension` or callable which returns a `Dimension`.
    :param height: `Dimension` or callable which returns a `Dimension`.

    :param left: Distance to the left edge of the `FloatContainer`.
    :param right: Distance to the right edge of the `FloatContainer`.
    :param top: Distance to the top of the `FloatContainer`.
    :param bottom: Distance to the bottom of the `FloatContainer`.

    :param attach_to_window: Attach to the cursor from this window, instead of
        the current window.
    :param hide_when_covering_content: Hide the float when it covers content underneath.
    :param allow_cover_cursor: When `False`, make sure to display the float
        below the cursor. Not on top of the indicated position.
    """
    def __init__(self, content=None, top=None, right=None, bottom=None, left=None,
                 width=None, height=None,
                 xcursor=None, ycursor=None, attach_to_window=None,
                 hide_when_covering_content=False, allow_cover_cursor=False):
        assert is_dimension(width)
        assert is_dimension(height)
        assert isinstance(hide_when_covering_content, bool)
        assert isinstance(allow_cover_cursor, bool)

        self.left = left
        self.right = right
        self.top = top
        self.bottom = bottom

        self.width = width
        self.height = height

        self.xcursor = xcursor
        self.ycursor = ycursor

        if attach_to_window:
            self.attach_to_window = to_window(attach_to_window)
        else:
            self.attach_to_window = None

        self.content = to_container(content)
        self.hide_when_covering_content = hide_when_covering_content
        self.allow_cover_cursor = allow_cover_cursor

    def get_width(self):
        if callable(self.width):
            return self.width()
        return self.width

    def get_height(self):
        if callable(self.height):
            return self.height()
        return self.height

    def __repr__(self):
        return 'Float(content=%r)' % self.content


class WindowRenderInfo(object):
    """
    Render information, for the last render time of this control.
    It stores mapping information between the input buffers (in case of a
    :class:`~prompt_toolkit.layout.controls.BufferControl`) and the actual
    render position on the output screen.

    (Could be used for implementation of the Vi 'H' and 'L' key bindings as
    well as implementing mouse support.)

    :param ui_content: The original :class:`.UIContent` instance that contains
        the whole input, without clipping. (ui_content)
    :param horizontal_scroll: The horizontal scroll of the :class:`.Window` instance.
    :param vertical_scroll: The vertical scroll of the :class:`.Window` instance.
    :param window_width: The width of the window that displays the content,
        without the margins.
    :param window_height: The height of the window that displays the content.
    :param configured_scroll_offsets: The scroll offsets as configured for the
        :class:`Window` instance.
    :param visible_line_to_row_col: Mapping that maps the row numbers on the
        displayed screen (starting from zero for the first visible line) to
        (row, col) tuples pointing to the row and column of the :class:`.UIContent`.
    :param rowcol_to_yx: Mapping that maps (row, column) tuples representing
        coordinates of the :class:`UIContent` to (y, x) absolute coordinates at
        the rendered screen.
    """
    def __init__(self, ui_content, horizontal_scroll, vertical_scroll,
                 window_width, window_height,
                 configured_scroll_offsets,
                 visible_line_to_row_col, rowcol_to_yx,
                 x_offset, y_offset, wrap_lines):
        assert isinstance(ui_content, UIContent)
        assert isinstance(horizontal_scroll, int)
        assert isinstance(vertical_scroll, int)
        assert isinstance(window_width, int)
        assert isinstance(window_height, int)
        assert isinstance(configured_scroll_offsets, ScrollOffsets)
        assert isinstance(visible_line_to_row_col, dict)
        assert isinstance(rowcol_to_yx, dict)
        assert isinstance(x_offset, int)
        assert isinstance(y_offset, int)
        assert isinstance(wrap_lines, bool)

        self.ui_content = ui_content
        self.vertical_scroll = vertical_scroll
        self.window_width = window_width  # Width without margins.
        self.window_height = window_height

        self.configured_scroll_offsets = configured_scroll_offsets
        self.visible_line_to_row_col = visible_line_to_row_col
        self.wrap_lines = wrap_lines

        self._rowcol_to_yx = rowcol_to_yx  # row/col from input to absolute y/x
                                           # screen coordinates.
        self._x_offset = x_offset
        self._y_offset = y_offset

    @property
    def visible_line_to_input_line(self):
        return dict(
            (visible_line, rowcol[0])
            for visible_line, rowcol in self.visible_line_to_row_col.items())

    @property
    def cursor_position(self):
        """
        Return the cursor position coordinates, relative to the left/top corner
        of the rendered screen.
        """
        cpos = self.ui_content.cursor_position
        y, x = self._rowcol_to_yx[cpos.y, cpos.x]
        return Point(x=x - self._x_offset, y=y - self._y_offset)

    @property
    def applied_scroll_offsets(self):
        """
        Return a :class:`.ScrollOffsets` instance that indicates the actual
        offset. This can be less than or equal to what's configured. E.g, when
        the cursor is completely at the top, the top offset will be zero rather
        than what's configured.
        """
        if self.displayed_lines[0] == 0:
            top = 0
        else:
            # Get row where the cursor is displayed.
            y = self.input_line_to_visible_line[self.ui_content.cursor_position.y]
            top = min(y, self.configured_scroll_offsets.top)

        return ScrollOffsets(
            top=top,
            bottom=min(self.ui_content.line_count - self.displayed_lines[-1] - 1,
                       self.configured_scroll_offsets.bottom),

            # For left/right, it probably doesn't make sense to return something.
            # (We would have to calculate the widths of all the lines and keep
            # double width characters in mind.)
            left=0, right=0)

    @property
    def displayed_lines(self):
        """
        List of all the visible rows. (Line numbers of the input buffer.)
        The last line may not be entirely visible.
        """
        return sorted(row for row, col in self.visible_line_to_row_col.values())

    @property
    def input_line_to_visible_line(self):
        """
        Return the dictionary mapping the line numbers of the input buffer to
        the lines of the screen. When a line spans several rows at the screen,
        the first row appears in the dictionary.
        """
        result = {}
        for k, v in self.visible_line_to_input_line.items():
            if v in result:
                result[v] = min(result[v], k)
            else:
                result[v] = k
        return result

    def first_visible_line(self, after_scroll_offset=False):
        """
        Return the line number (0 based) of the input document that corresponds
        with the first visible line.
        """
        if after_scroll_offset:
            return self.displayed_lines[self.applied_scroll_offsets.top]
        else:
            return self.displayed_lines[0]

    def last_visible_line(self, before_scroll_offset=False):
        """
        Like `first_visible_line`, but for the last visible line.
        """
        if before_scroll_offset:
            return self.displayed_lines[-1 - self.applied_scroll_offsets.bottom]
        else:
            return self.displayed_lines[-1]

    def center_visible_line(self, before_scroll_offset=False,
                            after_scroll_offset=False):
        """
        Like `first_visible_line`, but for the center visible line.
        """
        return (self.first_visible_line(after_scroll_offset) +
                (self.last_visible_line(before_scroll_offset) -
                 self.first_visible_line(after_scroll_offset)) // 2)

    @property
    def content_height(self):
        """
        The full height of the user control.
        """
        return self.ui_content.line_count

    @property
    def full_height_visible(self):
        """
        True when the full height is visible (There is no vertical scroll.)
        """
        return self.vertical_scroll == 0 and self.last_visible_line() == self.content_height

    @property
    def top_visible(self):
        """
        True when the top of the buffer is visible.
        """
        return self.vertical_scroll == 0

    @property
    def bottom_visible(self):
        """
        True when the bottom of the buffer is visible.
        """
        return self.last_visible_line() == self.content_height - 1

    @property
    def vertical_scroll_percentage(self):
        """
        Vertical scroll as a percentage. (0 means: the top is visible,
        100 means: the bottom is visible.)
        """
        if self.bottom_visible:
            return 100
        else:
            return (100 * self.vertical_scroll // self.content_height)

    def get_height_for_line(self, lineno):
        """
        Return the height of the given line.
        (The height that it would take, if this line became visible.)
        """
        if self.wrap_lines:
            return self.ui_content.get_height_for_line(lineno, self.window_width)
        else:
            return 1


class ScrollOffsets(object):
    """
    Scroll offsets for the :class:`.Window` class.

    Note that left/right offsets only make sense if line wrapping is disabled.
    """
    def __init__(self, top=0, bottom=0, left=0, right=0):
        assert isinstance(top, Integer)
        assert isinstance(bottom, Integer)
        assert isinstance(left, Integer)
        assert isinstance(right, Integer)

        self._top = top
        self._bottom = bottom
        self._left = left
        self._right = right

    @property
    def top(self):
        return int(self._top)

    @property
    def bottom(self):
        return int(self._bottom)

    @property
    def left(self):
        return int(self._left)

    @property
    def right(self):
        return int(self._right)

    def __repr__(self):
        return 'ScrollOffsets(top=%r, bottom=%r, left=%r, right=%r)' % (
            self.top, self.bottom, self.left, self.right)


class ColorColumn(object):
    def __init__(self, position, style=''):
        assert isinstance(position, int)
        assert isinstance(style, text_type)

        self.position = position
        self.style = style


_in_insert_mode = ViInsertMode() | EmacsInsertMode()


class Align:
    " Alignment of Window content. "
    LEFT = 'LEFT'
    RIGHT = 'RIGHT'
    CENTER = 'CENTER'
    _ALL = (LEFT, RIGHT, CENTER)


class Window(Container):
    """
    Container that holds a control.

    :param content: :class:`~prompt_toolkit.layout.controls.UIControl` instance.
    :param width: :class:`~prompt_toolkit.layout.dimension.Dimension` instance or callable.
    :param height: :class:`~prompt_toolkit.layout.dimension.Dimension` instance or callable.
    :param dont_extend_width: When `True`, don't take up more width then the
                              preferred width reported by the control.
    :param dont_extend_height: When `True`, don't take up more width then the
                               preferred height reported by the control.
    :param left_margins: A list of :class:`~prompt_toolkit.layout.margins.Margin`
        instance to be displayed on the left. For instance:
        :class:`~prompt_toolkit.layout.margins.NumberredMargin` can be one of
        them in order to show line numbers.
    :param right_margins: Like `left_margins`, but on the other side.
    :param scroll_offsets: :class:`.ScrollOffsets` instance, representing the
        preferred amount of lines/columns to be always visible before/after the
        cursor. When both top and bottom are a very high number, the cursor
        will be centered vertically most of the time.
    :param allow_scroll_beyond_bottom: A `bool` or
        :class:`~prompt_toolkit.filters.Filter` instance. When True, allow
        scrolling so far, that the top part of the content is not visible
        anymore, while there is still empty space available at the bottom of
        the window. In the Vi editor for instance, this is possible. You will
        see tildes while the top part of the body is hidden.
    :param wrap_lines: A `bool` or :class:`~prompt_toolkit.filters.Filter`
        instance. When True, don't scroll horizontally, but wrap lines instead.
    :param get_vertical_scroll: Callable that takes this window
        instance as input and returns a preferred vertical scroll.
        (When this is `None`, the scroll is only determined by the last and
        current cursor position.)
    :param get_horizontal_scroll: Callable that takes this window
        instance as input and returns a preferred vertical scroll.
    :param always_hide_cursor: A `bool` or
        :class:`~prompt_toolkit.filters.Filter` instance. When True, never
        display the cursor, even when the user control specifies a cursor
        position.
    :param cursorline: A `bool` or :class:`~prompt_toolkit.filters.Filter`
        instance. When True, display a cursorline.
    :param cursorcolumn: A `bool` or :class:`~prompt_toolkit.filters.Filter`
        instance. When True, display a cursorcolumn.
    :param get_colorcolumns: A callable that should return a a list of
        :class:`.ColorColumn` instances that describe the columns to be
        highlighted.
    :param align: alignment of content.
    :param style: A string string. Style to be applied to all the cells in this
        window.
    :param char: Character to be used for filling the background.
    :param transparent: When `False`, first erase everything underneath. (This
        is mainly useful if this Window is displayed inside a `Float`.)
        (when `char` or `get_char` is geven, it will never be transparant
        anyway, and this parameter doesn't change anything.)
    """
    def __init__(self, content=None, width=None, height=None,
                 dont_extend_width=False, dont_extend_height=False,
                 left_margins=None, right_margins=None, scroll_offsets=None,
                 allow_scroll_beyond_bottom=False, wrap_lines=False,
                 get_vertical_scroll=None, get_horizontal_scroll=None, always_hide_cursor=False,
                 cursorline=False, cursorcolumn=False, get_colorcolumns=None,
                 align=Align.LEFT, style='', char=None,
                 get_char=None, transparent=False):
        assert content is None or isinstance(content, UIControl)
        assert is_dimension(width)
        assert is_dimension(height)
        assert scroll_offsets is None or isinstance(scroll_offsets, ScrollOffsets)
        assert left_margins is None or all(isinstance(m, Margin) for m in left_margins)
        assert right_margins is None or all(isinstance(m, Margin) for m in right_margins)
        assert get_vertical_scroll is None or callable(get_vertical_scroll)
        assert get_horizontal_scroll is None or callable(get_horizontal_scroll)
        assert get_colorcolumns is None or callable(get_colorcolumns)
        assert align in Align._ALL
        assert isinstance(style, text_type)
        assert char is None or isinstance(char, text_type)
        assert get_char is None or callable(get_char)
        assert not (char and get_char)
        assert isinstance(transparent, bool)

        self.allow_scroll_beyond_bottom = to_filter(allow_scroll_beyond_bottom)
        self.always_hide_cursor = to_filter(always_hide_cursor)
        self.wrap_lines = to_filter(wrap_lines)
        self.cursorline = to_filter(cursorline)
        self.cursorcolumn = to_filter(cursorcolumn)

        self.content = content or DummyControl()
        self.dont_extend_width = dont_extend_width
        self.dont_extend_height = dont_extend_height
        self.left_margins = left_margins or []
        self.right_margins = right_margins or []
        self.scroll_offsets = scroll_offsets or ScrollOffsets()
        self.get_vertical_scroll = get_vertical_scroll
        self.get_horizontal_scroll = get_horizontal_scroll
        self.get_colorcolumns = get_colorcolumns or (lambda: [])
        self.align = align
        self.style = style
        self.char = char
        self.get_char = get_char
        self.transparent = transparent

        self.width = width
        self.height = height

        # Cache for the screens generated by the margin.
        self._ui_content_cache = SimpleCache(maxsize=8)
        self._margin_width_cache = SimpleCache(maxsize=1)

        self.reset()

    def __repr__(self):
        return 'Window(content=%r)' % self.content

    def reset(self):
        self.content.reset()

        #: Scrolling position of the main content.
        self.vertical_scroll = 0
        self.horizontal_scroll = 0

        # Vertical scroll 2: this is the vertical offset that a line is
        # scrolled if a single line (the one that contains the cursor) consumes
        # all of the vertical space.
        self.vertical_scroll_2 = 0

        #: Keep render information (mappings between buffer input and render
        #: output.)
        self.render_info = None

    def _get_margin_width(self, margin):
        """
        Return the width for this margin.
        (Calculate only once per render time.)
        """
        # Margin.get_width, needs to have a UIContent instance.
        def get_ui_content():
            return self._get_ui_content(width=0, height=0)

        def get_width():
            return margin.get_width(get_ui_content)

        key = (margin, get_app().render_counter)
        return self._margin_width_cache.get(key, get_width)

    def preferred_width(self, max_available_width):
        """
        Calculate the preferred width for this window.
        """
        def preferred_content_width():
            """ Content width: is only calculated if no exact width for the
            window was given. """
            # Calculate the width of the margin.
            total_margin_width = sum(self._get_margin_width(m) for m in
                                     self.left_margins + self.right_margins)

            # Window of the content. (Can be `None`.)
            preferred_width = self.content.preferred_width(
                max_available_width - total_margin_width)

            if preferred_width is not None:
                # Include width of the margins.
                preferred_width += total_margin_width
            return preferred_width

        # Merge.
        return self._merge_dimensions(
            dimension=to_dimension(self.width),
            get_preferred=preferred_content_width,
            dont_extend=self.dont_extend_width)

    def preferred_height(self, width, max_available_height):
        """
        Calculate the preferred height for this window.
        """
        def preferred_content_height():
            """ Content height: is only calculated if no exact height for the
            window was given. """
            total_margin_width = sum(self._get_margin_width(m) for m in
                                     self.left_margins + self.right_margins)
            wrap_lines = self.wrap_lines()

            return self.content.preferred_height(
                width - total_margin_width, max_available_height, wrap_lines)

        return self._merge_dimensions(
            dimension=to_dimension(self.height),
            get_preferred=preferred_content_height,
            dont_extend=self.dont_extend_height)

    @staticmethod
    def _merge_dimensions(dimension, get_preferred, dont_extend=False):
        """
        Take the Dimension from this `Window` class and the received preferred
        size from the `UIControl` and return a `Dimension` to report to the
        parent container.
        """
        dimension = dimension or Dimension()

        # When a preferred dimension was explicitly given to the Window,
        # ignore the UIControl.
        if dimension.preferred_specified:
            preferred = dimension.preferred
        else:
            # Otherwise, calculate the preferred dimension from the UI control
            # content.
            preferred = get_preferred()

        # When a 'preferred' dimension is given by the UIControl, make sure
        # that it stays within the bounds of the Window.
        if preferred is not None:
            if dimension.max_specified:
                preferred = min(preferred, dimension.max)

            if dimension.min_specified:
                preferred = max(preferred, dimension.min)

        # When a `dont_extend` flag has been given, use the preferred dimension
        # also as the max dimension.
        if dont_extend and preferred is not None:
            max_ = min(dimension.max, preferred)
        else:
            max_ = (dimension.max if dimension.max_specified else None)

        min_ = (dimension.min if dimension.min_specified else None)

        return Dimension(
            min=min_, max=max_,
            preferred=preferred, weight=dimension.weight)

    def _get_ui_content(self, width, height):
        """
        Create a `UIContent` instance.
        """
        def get_content():
            return self.content.create_content(width=width, height=height)

        key = (get_app().render_counter, width, height)
        return self._ui_content_cache.get(key, get_content)

    def _get_digraph_char(self):
        " Return `False`, or the Digraph symbol to be used. "
        app = get_app()
        if app.quoted_insert:
            return '^'
        if app.vi_state.waiting_for_digraph:
            if app.vi_state.digraph_symbol1:
                return app.vi_state.digraph_symbol1
            return '?'
        return False

    def write_to_screen(self, screen, mouse_handlers, write_position, parent_style, erase_bg):
        """
        Write window to screen. This renders the user control, the margins and
        copies everything over to the absolute position at the given screen.
        """
        # Don't bother writing invisible windows.
        # (We save some time, but also avoid applying last-line styling.)
        if write_position.height <= 0 or write_position.width <= 0:
            return

        # Calculate margin sizes.
        left_margin_widths = [self._get_margin_width(m) for m in self.left_margins]
        right_margin_widths = [self._get_margin_width(m) for m in self.right_margins]
        total_margin_width = sum(left_margin_widths + right_margin_widths)

        # Render UserControl.
        ui_content = self.content.create_content(
            write_position.width - total_margin_width, write_position.height)
        assert isinstance(ui_content, UIContent)

        # Scroll content.
        wrap_lines = self.wrap_lines()
        scroll_func = self._scroll_when_linewrapping if wrap_lines else self._scroll_without_linewrapping

        scroll_func(ui_content, write_position.width - total_margin_width, write_position.height)

        # Erase background and fill with `char`.
        self._fill_bg(screen, write_position, erase_bg)

        # Write body
        visible_line_to_row_col, rowcol_to_yx = self._copy_body(
            ui_content, screen, write_position,
            sum(left_margin_widths), write_position.width - total_margin_width,
            self.vertical_scroll, self.horizontal_scroll,
            wrap_lines=wrap_lines, highlight_lines=True,
            vertical_scroll_2=self.vertical_scroll_2,
            always_hide_cursor=self.always_hide_cursor(),
            has_focus=get_app().layout.current_control == self.content,
            align=self.align)

        # Remember render info. (Set before generating the margins. They need this.)
        x_offset=write_position.xpos + sum(left_margin_widths)
        y_offset=write_position.ypos

        self.render_info = WindowRenderInfo(
            ui_content=ui_content,
            horizontal_scroll=self.horizontal_scroll,
            vertical_scroll=self.vertical_scroll,
            window_width=write_position.width - total_margin_width,
            window_height=write_position.height,
            configured_scroll_offsets=self.scroll_offsets,
            visible_line_to_row_col=visible_line_to_row_col,
            rowcol_to_yx=rowcol_to_yx,
            x_offset=x_offset,
            y_offset=y_offset,
            wrap_lines=wrap_lines)

        # Set mouse handlers.
        def mouse_handler(mouse_event):
            """ Wrapper around the mouse_handler of the `UIControl` that turns
            screen coordinates into line coordinates. """
            # Don't handle mouse events outside of the current modal part of
            # the UI.
            if self not in get_app().layout.walk_through_modal_area():
                return

            # Find row/col position first.
            yx_to_rowcol = dict((v, k) for k, v in rowcol_to_yx.items())
            y = mouse_event.position.y
            x = mouse_event.position.x

            # If clicked below the content area, look for a position in the
            # last line instead.
            max_y = write_position.ypos + len(visible_line_to_row_col) - 1
            y = min(max_y, y)

            while x >= 0:
                try:
                    row, col = yx_to_rowcol[y, x]
                except KeyError:
                    # Try again. (When clicking on the right side of double
                    # width characters, or on the right side of the input.)
                    x -= 1
                else:
                    # Found position, call handler of UIControl.
                    result = self.content.mouse_handler(
                        MouseEvent(position=Point(x=col, y=row),
                                   event_type=mouse_event.event_type))
                    break
            else:
                # nobreak.
                # (No x/y coordinate found for the content. This happens in
                # case of a DummyControl, that does not have any content.
                # Report (0,0) instead.)
                result = self.content.mouse_handler(
                    MouseEvent(position=Point(x=0, y=0),
                               event_type=mouse_event.event_type))

            # If it returns NotImplemented, handle it here.
            if result == NotImplemented:
                return self._mouse_handler(mouse_event)

            return result

        mouse_handlers.set_mouse_handler_for_range(
            x_min=write_position.xpos + sum(left_margin_widths),
            x_max=write_position.xpos + write_position.width - total_margin_width,
            y_min=write_position.ypos,
            y_max=write_position.ypos + write_position.height,
            handler=mouse_handler)

        # Render and copy margins.
        move_x = 0

        def render_margin(m, width):
            " Render margin. Return `Screen`. "
            # Retrieve margin fragments.
            fragments = m.create_margin(self.render_info, width, write_position.height)

            # Turn it into a UIContent object.
            # already rendered those fragments using this size.)
            return FormattedTextControl(fragments).create_content(
                width + 1, write_position.height)

        for m, width in zip(self.left_margins, left_margin_widths):
            if width > 0:  # (ConditionalMargin returns a zero width. -- Don't render.)
                # Create screen for margin.
                margin_screen = render_margin(m, width)

                # Copy and shift X.
                self._copy_margin(margin_screen, screen, write_position, move_x, width)
                move_x += width

        move_x = write_position.width - sum(right_margin_widths)

        for m, width in zip(self.right_margins, right_margin_widths):
            # Create screen for margin.
            margin_screen = render_margin(m, width)

            # Copy and shift X.
            self._copy_margin(margin_screen, screen, write_position, move_x, width)
            move_x += width

        # Apply 'self.style'
        self._apply_style(screen, write_position, parent_style)

        # Tell the screen that this user control has been painted.
        screen.visible_windows.append(self)

    def _copy_body(self, ui_content, new_screen, write_position, move_x,
                   width, vertical_scroll=0, horizontal_scroll=0,
                   wrap_lines=False, highlight_lines=False,
                   vertical_scroll_2=0, always_hide_cursor=False,
                   has_focus=False, align=Align.LEFT):
        """
        Copy the UIContent into the output screen.
        """
        xpos = write_position.xpos + move_x
        ypos = write_position.ypos
        line_count = ui_content.line_count
        new_buffer = new_screen.data_buffer
        empty_char = _CHAR_CACHE['', '']

        # Map visible line number to (row, col) of input.
        # 'col' will always be zero if line wrapping is off.
        visible_line_to_row_col = {}
        rowcol_to_yx = {}  # Maps (row, col) from the input to (y, x) screen coordinates.

        # Copy content.
        def copy():
            y = - vertical_scroll_2
            lineno = vertical_scroll

            while y < write_position.height and lineno < line_count:
                # Take the next line and copy it in the real screen.
                line = ui_content.get_line(lineno)

                col = 0
                x = -horizontal_scroll

                visible_line_to_row_col[y] = (lineno, horizontal_scroll)
                new_buffer_row = new_buffer[y + ypos]

                # Align this line.
                if align == Align.CENTER:
                    line_width = fragment_list_width(line)
                    if line_width < width:
                        x += (width - line_width) // 2
                elif align == Align.RIGHT:
                    line_width = fragment_list_width(line)
                    if line_width < width:
                        x += (width - line_width) + 1

                # Copy line.
                for style, text in line:
                    # Remember raw VT escape sequences. (E.g. FinalTerm's
                    # escape sequences.)
                    if '[ZeroWidthEscape]' in style:
                        new_screen.zero_width_escapes[y + ypos][x + xpos] += text
                        continue

                    for c in text:
                        char = _CHAR_CACHE[c, style]
                        char_width = char.width

                        # Wrap when the line width is exceeded.
                        if wrap_lines and x + char_width > width:
                            visible_line_to_row_col[y + 1] = (
                                lineno, visible_line_to_row_col[y][1] + x)
                            y += 1
                            x = -horizontal_scroll  # This would be equal to zero.
                                                    # (horizontal_scroll=0 when wrap_lines.)
                            new_buffer_row = new_buffer[y + ypos]

                            if y >= write_position.height:
                                return y  # Break out of all for loops.

                        # Set character in screen and shift 'x'.
                        if x >= 0 and y >= 0 and x < write_position.width:
                            new_buffer_row[x + xpos] = char

                            # When we print a multi width character, make sure
                            # to erase the neighbous positions in the screen.
                            # (The empty string if different from everything,
                            # so next redraw this cell will repaint anyway.)
                            if char_width > 1:
                                for i in range(1, char_width):
                                    new_buffer_row[x + xpos + i] = empty_char

                            # If this is a zero width characters, then it's
                            # probably part of a decomposed unicode character.
                            # See: https://en.wikipedia.org/wiki/Unicode_equivalence
                            # Merge it in the previous cell.
                            elif char_width == 0 and x - 1 >= 0:
                                prev_char = new_buffer_row[x + xpos - 1]
                                char2 = _CHAR_CACHE[prev_char.char + c, prev_char.style]
                                new_buffer_row[x + xpos - 1] = char2

                            # Keep track of write position for each character.
                            rowcol_to_yx[lineno, col] = (y + ypos, x + xpos)

                        col += 1
                        x += char_width

                lineno += 1
                y += 1
            return y

        y = copy()

        def cursor_pos_to_screen_pos(row, col):
            " Translate row/col from UIContent to real Screen coordinates. "
            try:
                y, x = rowcol_to_yx[row, col]
            except KeyError:
                # Normally this should never happen. (It is a bug, if it happens.)
                # But to be sure, return (0, 0)
                return Point(x=0, y=0)

                # raise ValueError(
                #     'Invalid position. row=%r col=%r, vertical_scroll=%r, '
                #     'horizontal_scroll=%r, height=%r' %
                #     (row, col, vertical_scroll, horizontal_scroll, write_position.height))
            else:
                return Point(x=x, y=y)

        # Set cursor and menu positions.
        if ui_content.cursor_position:
            screen_cursor_position = cursor_pos_to_screen_pos(
                    ui_content.cursor_position.y, ui_content.cursor_position.x)

            if has_focus:
                new_screen.set_cursor_position(self, screen_cursor_position)

                if always_hide_cursor:
                    new_screen.show_cursor = False
                else:
                    new_screen.show_cursor = ui_content.show_cursor

                self._highlight_digraph(new_screen)

            if highlight_lines:
                self._highlight_cursorlines(
                    new_screen, screen_cursor_position, xpos, ypos, width,
                    write_position.height)

        # Draw input characters from the input processor queue.
        if has_focus and ui_content.cursor_position:
            self._show_key_processor_key_buffer(new_screen)

        # Set menu position.
        if ui_content.menu_position:
            new_screen.set_menu_position(self, cursor_pos_to_screen_pos(
                    ui_content.menu_position.y, ui_content.menu_position.x))

        # Update output screne height.
        new_screen.height = max(new_screen.height, ypos + write_position.height)

        return visible_line_to_row_col, rowcol_to_yx

    def _fill_bg(self, screen, write_position, erase_bg):
        """
        Erase/fill the background.
        (Useful for floats and when a `char` has been given.)
        """
        if self.get_char:
            char = self.get_char()
        else:
            char = self.char

        if (erase_bg or char) and not self.transparent:
            wp = write_position
            char = _CHAR_CACHE[char or ' ', '']

            for y in range(wp.ypos, wp.ypos + wp.height):
                row = screen.data_buffer[y]
                for x in range(wp.xpos, wp.xpos + wp.width):
                    row[x] = char

    def _apply_style(self, new_screen, write_position, parent_style):
        # Apply `self.style`.
        style = parent_style + ' ' + self.style

        if get_app().layout.current_window == self:
            style += ' class:focussed'
        else:
            style += ' class:not-focussed'

        new_screen.fill_area(write_position, style=style, after=False)

        # Apply the 'last-line' class to the last line of each Window. This can
        # be used to apply an 'underline' to the user control.
        wp = WritePosition(write_position.xpos, write_position.ypos + write_position.height - 1,
                           write_position.width, 1)
        new_screen.fill_area(wp, 'class:last-line', after=True)

    def _highlight_digraph(self, new_screen):
        """
        When we are in Vi digraph mode, put a question mark underneath the
        cursor.
        """
        digraph_char = self._get_digraph_char()
        if digraph_char:
            cpos = new_screen.get_cursor_position(self)
            new_screen.data_buffer[cpos.y][cpos.x] = \
                _CHAR_CACHE[digraph_char, 'class:digraph']

    def _show_key_processor_key_buffer(self, new_screen):
        """
        When the user is typing a key binding that consists of several keys,
        display the last pressed key if the user is in insert mode and the key
        is meaningful to be displayed.
        E.g. Some people want to bind 'jj' to escape in Vi insert mode. But the
             first 'j' needs to be displayed in order to get some feedback.
        """
        app = get_app()
        key_buffer = app.key_processor.key_buffer

        if key_buffer and _in_insert_mode() and not app.is_done:
            # The textual data for the given key. (Can be a VT100 escape
            # sequence.)
            data = key_buffer[-1].data

            # Display only if this is a 1 cell width character.
            if get_cwidth(data) == 1:
                cpos = new_screen.get_cursor_position(self)
                new_screen.data_buffer[cpos.y][cpos.x] = \
                    _CHAR_CACHE[data, 'class:partial-key-binding']

    def _highlight_cursorlines(self, new_screen, cpos, x, y, width, height):
        """
        Highlight cursor row/column.
        """
        cursor_line_style = ' class:cursor-line '
        cursor_column_style = ' class:cursor-column '

        data_buffer = new_screen.data_buffer

        # Highlight cursor line.
        if self.cursorline():
            row = data_buffer[cpos.y]
            for x in range(x, x + width):
                original_char = row[x]
                row[x] = _CHAR_CACHE[
                    original_char.char, original_char.style + cursor_line_style]

        # Highlight cursor column.
        if self.cursorcolumn():
            for y2 in range(y, y + height):
                row = data_buffer[y2]
                original_char = row[cpos.x]
                row[cpos.x] = _CHAR_CACHE[
                   original_char.char, original_char.style + cursor_column_style]

        # Highlight color columns
        for cc in self.get_colorcolumns():
            assert isinstance(cc, ColorColumn)
            color_column_style = ' ' + cc.style
            column = cc.position

            for y2 in range(y, y + height):
                row = data_buffer[y2]
                original_char = row[column]
                row[column] = _CHAR_CACHE[
                   original_char.char, original_char.style + color_column_style]

    def _copy_margin(self, lazy_screen, new_screen, write_position, move_x, width):
        """
        Copy characters from the margin screen to the real screen.
        """
        xpos = write_position.xpos + move_x
        ypos = write_position.ypos

        margin_write_position = WritePosition(xpos, ypos, width, write_position.height)
        self._copy_body(lazy_screen, new_screen, margin_write_position, 0, width)

    def _scroll_when_linewrapping(self, ui_content, width, height):
        """
        Scroll to make sure the cursor position is visible and that we maintain
        the requested scroll offset.

        Set `self.horizontal_scroll/vertical_scroll`.
        """
        scroll_offsets_bottom = self.scroll_offsets.bottom
        scroll_offsets_top = self.scroll_offsets.top

        # We don't have horizontal scrolling.
        self.horizontal_scroll = 0

        # If the current line consumes more than the whole window height,
        # then we have to scroll vertically inside this line. (We don't take
        # the scroll offsets into account for this.)
        # Also, ignore the scroll offsets in this case. Just set the vertical
        # scroll to this line.
        if ui_content.get_height_for_line(ui_content.cursor_position.y, width) > height - scroll_offsets_top:
            # Calculate the height of the text before the cursor, with the line
            # containing the cursor included, and the character belowe the
            # cursor included as well.
            line = explode_text_fragments(ui_content.get_line(ui_content.cursor_position.y))
            text_before_cursor = fragment_list_to_text(line[:ui_content.cursor_position.x + 1])
            text_before_height = UIContent.get_height_for_text(text_before_cursor, width)

            # Adjust scroll offset.
            self.vertical_scroll = ui_content.cursor_position.y
            self.vertical_scroll_2 = min(text_before_height - 1, self.vertical_scroll_2)
            self.vertical_scroll_2 = max(0, text_before_height - height, self.vertical_scroll_2)
            return
        else:
            self.vertical_scroll_2 = 0

        # Current line doesn't consume the whole height. Take scroll offsets into account.
        def get_min_vertical_scroll():
            # Make sure that the cursor line is not below the bottom.
            # (Calculate how many lines can be shown between the cursor and the .)
            used_height = 0
            prev_lineno = ui_content.cursor_position.y

            for lineno in range(ui_content.cursor_position.y, -1, -1):
                used_height += ui_content.get_height_for_line(lineno, width)

                if used_height > height - scroll_offsets_bottom:
                    return prev_lineno
                else:
                    prev_lineno = lineno
            return 0

        def get_max_vertical_scroll():
            # Make sure that the cursor line is not above the top.
            prev_lineno = ui_content.cursor_position.y
            used_height = 0

            for lineno in range(ui_content.cursor_position.y - 1, -1, -1):
                used_height += ui_content.get_height_for_line(lineno, width)

                if used_height > scroll_offsets_top:
                    return prev_lineno
                else:
                    prev_lineno = lineno
            return prev_lineno

        def get_topmost_visible():
            """
            Calculate the upper most line that can be visible, while the bottom
            is still visible. We should not allow scroll more than this if
            `allow_scroll_beyond_bottom` is false.
            """
            prev_lineno = ui_content.line_count - 1
            used_height = 0
            for lineno in range(ui_content.line_count - 1, -1, -1):
                used_height += ui_content.get_height_for_line(lineno, width)
                if used_height > height:
                    return prev_lineno
                else:
                    prev_lineno = lineno
            return prev_lineno

        # Scroll vertically. (Make sure that the whole line which contains the
        # cursor is visible.
        topmost_visible = get_topmost_visible()

            # Note: the `min(topmost_visible, ...)` is to make sure that we
            # don't require scrolling up because of the bottom scroll offset,
            # when we are at the end of the document.
        self.vertical_scroll = max(self.vertical_scroll, min(topmost_visible, get_min_vertical_scroll()))
        self.vertical_scroll = min(self.vertical_scroll, get_max_vertical_scroll())

        # Disallow scrolling beyond bottom?
        if not self.allow_scroll_beyond_bottom():
            self.vertical_scroll = min(self.vertical_scroll, topmost_visible)

    def _scroll_without_linewrapping(self, ui_content, width, height):
        """
        Scroll to make sure the cursor position is visible and that we maintain
        the requested scroll offset.

        Set `self.horizontal_scroll/vertical_scroll`.
        """
        cursor_position = ui_content.cursor_position or Point(x=0, y=0)

        # Without line wrapping, we will never have to scroll vertically inside
        # a single line.
        self.vertical_scroll_2 = 0

        if ui_content.line_count == 0:
            self.vertical_scroll = 0
            self.horizontal_scroll = 0
            return
        else:
            current_line_text = fragment_list_to_text(ui_content.get_line(cursor_position.y))

        def do_scroll(current_scroll, scroll_offset_start, scroll_offset_end,
                      cursor_pos, window_size, content_size):
            " Scrolling algorithm. Used for both horizontal and vertical scrolling. "
            # Calculate the scroll offset to apply.
            # This can obviously never be more than have the screen size. Also, when the
            # cursor appears at the top or bottom, we don't apply the offset.
            scroll_offset_start = int(min(scroll_offset_start, window_size / 2, cursor_pos))
            scroll_offset_end = int(min(scroll_offset_end, window_size / 2,
                                        content_size - 1 - cursor_pos))

            # Prevent negative scroll offsets.
            if current_scroll < 0:
                current_scroll = 0

            # Scroll back if we scrolled to much and there's still space to show more of the document.
            if (not self.allow_scroll_beyond_bottom() and
                    current_scroll > content_size - window_size):
                current_scroll = max(0, content_size - window_size)

            # Scroll up if cursor is before visible part.
            if current_scroll > cursor_pos - scroll_offset_start:
                current_scroll = max(0, cursor_pos - scroll_offset_start)

            # Scroll down if cursor is after visible part.
            if current_scroll < (cursor_pos + 1) - window_size + scroll_offset_end:
                current_scroll = (cursor_pos + 1) - window_size + scroll_offset_end

            return current_scroll

        # When a preferred scroll is given, take that first into account.
        if self.get_vertical_scroll:
            self.vertical_scroll = self.get_vertical_scroll(self)
            assert isinstance(self.vertical_scroll, int)
        if self.get_horizontal_scroll:
            self.horizontal_scroll = self.get_horizontal_scroll(self)
            assert isinstance(self.horizontal_scroll, int)

        # Update horizontal/vertical scroll to make sure that the cursor
        # remains visible.
        offsets = self.scroll_offsets

        self.vertical_scroll = do_scroll(
            current_scroll=self.vertical_scroll,
            scroll_offset_start=offsets.top,
            scroll_offset_end=offsets.bottom,
            cursor_pos=ui_content.cursor_position.y,
            window_size=height,
            content_size=ui_content.line_count)

        self.horizontal_scroll = do_scroll(
            current_scroll=self.horizontal_scroll,
            scroll_offset_start=offsets.left,
            scroll_offset_end=offsets.right,
            cursor_pos=get_cwidth(current_line_text[:ui_content.cursor_position.x]),
            window_size=width,
            # We can only analyse the current line. Calculating the width off
            # all the lines is too expensive.
            content_size=max(get_cwidth(current_line_text), self.horizontal_scroll + width))

    def _mouse_handler(self, mouse_event):
        """
        Mouse handler. Called when the UI control doesn't handle this
        particular event.
        """
        if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
            self._scroll_down()
        elif mouse_event.event_type == MouseEventType.SCROLL_UP:
            self._scroll_up()

    def _scroll_down(self):
        " Scroll window down. "
        info = self.render_info

        if self.vertical_scroll < info.content_height - info.window_height:
            if info.cursor_position.y <= info.configured_scroll_offsets.top:
                self.content.move_cursor_down()

            self.vertical_scroll += 1

    def _scroll_up(self):
        " Scroll window up. "
        info = self.render_info

        if info.vertical_scroll > 0:
            # TODO: not entirely correct yet in case of line wrapping and long lines.
            if info.cursor_position.y >= info.window_height - 1 - info.configured_scroll_offsets.bottom:
                self.content.move_cursor_up()

            self.vertical_scroll -= 1

    def get_key_bindings(self):
        return self.content.get_key_bindings()

    def get_children(self):
        return []


class ConditionalContainer(Container):
    """
    Wrapper around any other container that can change the visibility. The
    received `filter` determines whether the given container should be
    displayed or not.

    :param content: :class:`.Container` instance.
    :param filter: :class:`~prompt_toolkit.filters.Filter` instance.
    """
    def __init__(self, content, filter):
        self.content = to_container(content)
        self.filter = to_filter(filter)

    def __repr__(self):
        return 'ConditionalContainer(%r, filter=%r)' % (self.content, self.filter)

    def reset(self):
        self.content.reset()

    def preferred_width(self, max_available_width):
        if self.filter():
            return self.content.preferred_width(max_available_width)
        else:
            return Dimension.zero()

    def preferred_height(self, width, max_available_height):
        if self.filter():
            return self.content.preferred_height(width, max_available_height)
        else:
            return Dimension.zero()

    def write_to_screen(self, screen, mouse_handlers, write_position, parent_style, erase_bg):
        if self.filter():
            return self.content.write_to_screen(
                screen, mouse_handlers, write_position, parent_style, erase_bg)

    def get_children(self):
        return [self.content]


def to_container(container):
    """
    Make sure that the given object is a `Container`.
    """
    if isinstance(container, Container):
        return container
    elif hasattr(container, '__pt_container__'):
        return to_container(container.__pt_container__())
    else:
        raise ValueError('Not a container object.')


def to_window(container):
    """
    Make sure that the given argument is a `Window`.
    """
    if isinstance(container, Window):
        return container
    elif hasattr(container, '__pt_container__'):
        return to_window(container.__pt_container__())
    else:
        raise ValueError('Not a Window object: %r.' % (container, ))


def is_container(value):
    """
    Checks whether the given value is a container object
    (for use in assert statements).
    """
    if isinstance(value, Container):
        return True
    if hasattr(value, '__pt_container__'):
        return is_container(value.__pt_container__())
    return False
