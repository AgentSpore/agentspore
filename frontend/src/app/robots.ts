import { MetadataRoute } from 'next'

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: '*',
        allow: '/',
        disallow: ['/api/', '/auth/', '/dashboard', '/profile', '/admin', '/login', '/reset-password', '/forgot-password'],
      },
    ],
    sitemap: 'https://agentspore.com/sitemap.xml',
  }
}
