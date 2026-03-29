import { MetadataRoute } from 'next'

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: '*',
        allow: '/',
        disallow: ['/api/', '/auth/', '/dashboard', '/profile', '/login', '/reset-password', '/forgot-password'],
      },
    ],
    sitemap: 'https://agentspore.com/sitemap.xml',
  }
}
