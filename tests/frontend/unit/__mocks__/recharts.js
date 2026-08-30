/**
 * Lightweight recharts mock for jest.
 * Recharts uses browser-only APIs (SVG, ResizeObserver) that aren't
 * available in jsdom, so we replace all exports with simple pass-through
 * React components.
 */
const React = require("react");

const noop = () => null;
const passthrough = ({children}) => React.createElement("div", null, children);

module.exports = {
    LineChart: passthrough,
    BarChart: passthrough,
    AreaChart: passthrough,
    PieChart: passthrough,
    Line: noop,
    Bar: noop,
    Area: noop,
    Pie: noop,
    Cell: noop,
    XAxis: noop,
    YAxis: noop,
    CartesianGrid: noop,
    Tooltip: noop,
    Legend: noop,
    ResponsiveContainer: passthrough,
    ReferenceLine: noop,
    ReferenceArea: noop,
};
