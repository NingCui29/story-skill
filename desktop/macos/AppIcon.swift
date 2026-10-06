import AppKit

let destination = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
let variants = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2), (256, 1), (256, 2), (512, 1), (512, 2)]
for (size, scale) in variants {
    let pixels = size * scale
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
                                 bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                 isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0,
                                 bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    let context = NSGraphicsContext.current!.cgContext
    context.scaleBy(x: CGFloat(pixels) / 1024, y: CGFloat(pixels) / 1024)
    NSColor(calibratedRed: 0.16, green: 0.24, blue: 0.31, alpha: 1).setFill()
    NSBezierPath(roundedRect: NSRect(x: 50, y: 50, width: 924, height: 924),
                 xRadius: 190, yRadius: 190).fill()
    NSColor(calibratedRed: 0.92, green: 0.87, blue: 0.74, alpha: 1).setFill()
    let left = NSBezierPath()
    left.move(to: NSPoint(x: 210, y: 690)); left.line(to: NSPoint(x: 210, y: 305))
    left.curve(to: NSPoint(x: 492, y: 265), controlPoint1: NSPoint(x: 295, y: 330),
               controlPoint2: NSPoint(x: 412, y: 315))
    left.line(to: NSPoint(x: 492, y: 650))
    left.curve(to: NSPoint(x: 210, y: 690), controlPoint1: NSPoint(x: 412, y: 710),
               controlPoint2: NSPoint(x: 295, y: 720)); left.close(); left.fill()
    let right = NSBezierPath()
    right.move(to: NSPoint(x: 814, y: 690)); right.line(to: NSPoint(x: 814, y: 305))
    right.curve(to: NSPoint(x: 532, y: 265), controlPoint1: NSPoint(x: 729, y: 330),
                controlPoint2: NSPoint(x: 612, y: 315))
    right.line(to: NSPoint(x: 532, y: 650))
    right.curve(to: NSPoint(x: 814, y: 690), controlPoint1: NSPoint(x: 612, y: 710),
                controlPoint2: NSPoint(x: 729, y: 720)); right.close(); right.fill()
    NSGraphicsContext.restoreGraphicsState()
    let name = "icon_\(size)x\(size)" + (scale == 2 ? "@2x" : "") + ".png"
    try bitmap.representation(using: .png, properties: [:])!.write(to: destination.appendingPathComponent(name))
}
